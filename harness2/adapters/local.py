"""`LocalTasksAdapter`: upstream's BenchmarkAdapter protocol over a local tasks directory.

Every method maps to `harness2.tasks` (what the task is) or to the Claude Code substrate (how
one rollout runs and what it left behind). Nothing here is method logic. Rollouts live at
`<runs>/rollouts/<run_id>/`; since a run id embeds the task id, the adapter also records the
task id in `<run_dir>/adapter.json` (after the substrate returns) so a run directory can be
read back on its own.
"""

from __future__ import annotations

import dataclasses
import json
import subprocess
import sys
import threading
from collections import Counter
from pathlib import Path

from .. import config, tasks
from ..core import judge
from ..core.spec import BenchPrompts, BenchSpec, ChannelSpec

ADAPTER_FILE = "adapter.json"

LOCAL_PROMPTS = BenchPrompts(
    work=(
        "complete a self-contained piece of work inside a project directory with Claude Code: "
        "read the instructions and the files provided, do the work with the available tools, "
        "and write every requested deliverable to the path the instructions name"
    ),
    domain_word="domain",
    deliverable_word="deliverable",
)

_PROMPT_FIELDS = tuple(f.name for f in dataclasses.fields(BenchPrompts))


class LocalTasksAdapter:
    """The method's view of `<tasks_root>/<domain>/<task>/` tasks run under Claude Code."""

    def __init__(
        self,
        tasks_root: Path | str,
        *,
        substrate,
        base_harness: Path | str,
        runs_root: Path | str,
        split_file: Path | str | None = None,
    ):
        self.tasks_root = Path(tasks_root)
        self.substrate = substrate
        self._base_harness = Path(base_harness)
        self.split_file = Path(split_file) if split_file is not None else None
        channel = ChannelSpec("claude_cli", config.IMPROVER_MODEL, effort=config.IMPROVER_EFFORT)
        self.spec = BenchSpec(
            name="local",
            runs_root=Path(runs_root),
            install_mode="workspace_config",
            prompts=LOCAL_PROMPTS,
            channels={"proposer": channel, "selector": channel},
            leak_globs=tasks.LEAK_GLOBS,
            allow_subagents=True,
            obs_trunc=50_000,
        )
        self._cache: dict[str, tasks.Task] = {}
        self._lock = threading.Lock()

    # ---- tasks ---------------------------------------------------------------------------

    def task(self, task_id: str) -> tasks.Task:
        """The loaded task, cached per process (task files are read-only during a run)."""
        with self._lock:
            got = self._cache.get(task_id)
        if got is None:
            got = tasks.load(self.tasks_root, task_id)
            with self._lock:
                self._cache[task_id] = got
        return got

    def tasks(self, split: str = "all") -> list[str]:
        return tasks.select(tasks.discover(self.tasks_root), split, self.split_file)

    def domain_of(self, task_id: str) -> str:
        return str(task_id).split("/", 1)[0]

    def reference_domain_order(self) -> list[str]:
        """Descending task count, then name, over every domain on disk."""
        counts = Counter(tasks.discover(self.tasks_root).values())
        return sorted(counts, key=lambda d: (-counts[d], d))

    def task_prompt(self, task_id: str) -> str:
        return self.task(task_id).instruction

    def task_files(self, task_id: str) -> Path | None:
        return self.task(task_id).workspace

    def prompts_for(self, task_id: str) -> BenchPrompts:
        """The spec's prompts with an optional `<task>/prompts.json` merged over them."""
        path = self.task(task_id).dir / "prompts.json"
        if not path.is_file():
            return self.spec.prompts
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            raise ValueError(f"{path} is not valid JSON: {exc}") from exc
        if not isinstance(raw, dict):
            raise ValueError(f"{path} must hold a JSON object of BenchPrompts fields")
        unknown = sorted(set(raw) - set(_PROMPT_FIELDS))
        if unknown:
            raise ValueError(f"{path}: unknown keys {unknown}; allowed: {list(_PROMPT_FIELDS)}")
        bad = sorted(key for key, value in raw.items() if not isinstance(value, str))
        if bad:
            raise ValueError(f"{path}: values must be strings, not for {bad}")
        return dataclasses.replace(self.spec.prompts, **raw)

    # ---- rollouts ------------------------------------------------------------------------

    def run_dir(self, run_id: str) -> Path:
        return self.spec.runs_root / "rollouts" / run_id

    def _write_identity(self, run_dir: Path, task_id: str) -> None:
        p = run_dir / ADAPTER_FILE
        if p.is_file():
            try:
                if json.loads(p.read_text(encoding="utf-8")).get("task_id") == task_id:
                    return
            except (OSError, ValueError):
                pass
        run_dir.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps({"task_id": task_id}, indent=2) + "\n", encoding="utf-8")

    def task_of(self, run_dir: Path) -> str:
        """The task id a run directory belongs to: adapter.json, else the run-id path."""
        run_dir = Path(run_dir)
        try:
            got = json.loads((run_dir / ADAPTER_FILE).read_text(encoding="utf-8"))
            if isinstance(got, dict) and got.get("task_id"):
                return str(got["task_id"])
        except (OSError, ValueError):
            pass
        # <runs>/rollouts/<ns>/<domain>/<task>/<label>
        root = self.spec.runs_root / "rollouts"
        try:
            parts = run_dir.resolve().relative_to(root.resolve()).parts
        except ValueError:
            parts = run_dir.parts[-4:]
        if len(parts) >= 4:
            return f"{parts[-3]}/{parts[-2]}"
        raise ValueError(f"cannot tell which task {run_dir} belongs to (no {ADAPTER_FILE})")

    def execute(
        self, task_id: str, harness_dir: Path | None, run_id: str, *, timeout: int | None = None
    ) -> Path:
        task = self.task(task_id)
        run_dir = self.run_dir(run_id)
        # The substrate owns run_dir (it clears a non-reusable one), so the task id is
        # recorded after it returns; task_of() falls back to the run-id path before that.
        out = self.substrate.execute(
            task,
            harness_dir,
            run_dir,
            base_harness=self.base_harness(),
            timeout=timeout or task.timeout_s,
        )
        out = Path(out)
        self._write_identity(out, task_id)
        return out

    def _deliverables(self, run_dir: Path) -> list[str]:
        return list(self.task(self.task_of(run_dir)).deliverables)

    def delivered(self, run_dir: Path) -> bool:
        return bool(self.substrate.delivered(Path(run_dir), self._deliverables(run_dir)))

    def trajectory(self, run_dir: Path, *, obs_trunc: int) -> str:
        return self.substrate.trajectory(Path(run_dir), obs_trunc=obs_trunc)

    def deliverable_files(self, run_dir: Path) -> dict[str, str]:
        return self.substrate.deliverable_files(Path(run_dir), self._deliverables(run_dir))

    def deliverable_text(self, run_dir: Path) -> str:
        return self.substrate.deliverable_text(Path(run_dir), self._deliverables(run_dir))

    # ---- harness ---------------------------------------------------------------------------

    def base_harness(self) -> Path:
        return self._base_harness

    def validate_tree(self, tree: dict[str, str]) -> None:
        """Local tasks add no invariants beyond the surface's own validation."""
        return None

    # ---- judging (only after selection) -----------------------------------------------------

    def judge_kind(self, task_id: str) -> str | None:
        """ "check" (check.py), "rubric" (rubric.json) or None when the task has no judge."""
        d = self.task(task_id).dir
        if (d / "check.py").is_file():
            return "check"
        if (d / "rubric.json").is_file():
            return "rubric"
        return None

    def score(self, run_dir: Path, task_id: str) -> dict | None:
        """check.py if present, else the rubric judge, else None. None is never a zero."""
        kind = self.judge_kind(task_id)
        if kind == "check":
            return self._run_check(Path(run_dir), task_id)
        if kind == "rubric":
            return self._run_rubric(Path(run_dir), task_id)
        return None

    def _run_check(self, run_dir: Path, task_id: str) -> dict | None:
        task = self.task(task_id)
        try:
            p = subprocess.run(
                [sys.executable, str(task.dir / "check.py"), str(run_dir / "deliverables")],
                capture_output=True,
                text=True,
                cwd=task.dir,
                timeout=600,
            )
        except (OSError, subprocess.SubprocessError):
            return None
        got = None
        for line in reversed((p.stdout or "").strip().splitlines()):
            line = line.strip()
            if line.startswith("{"):
                try:
                    got = json.loads(line)
                    break
                except json.JSONDecodeError:
                    continue
        if got is None:
            try:
                got = json.loads(p.stdout)
            except (json.JSONDecodeError, TypeError):
                return None
        if not isinstance(got, dict):
            return None
        n_passed, n_criteria = got.get("n_passed"), got.get("n_criteria")
        if not all(isinstance(v, int) and not isinstance(v, bool) for v in (n_passed, n_criteria)):
            return None
        if n_criteria <= 0 or not 0 <= n_passed <= n_criteria:
            return None
        return {**got, "all_pass": n_passed == n_criteria, "metric": "check"}

    def _run_rubric(self, run_dir: Path, task_id: str) -> dict | None:
        task = self.task(task_id)
        try:
            raw = json.loads((task.dir / "rubric.json").read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None
        criteria = raw.get("criteria") if isinstance(raw, dict) else raw
        if not isinstance(criteria, list) or not criteria:
            return None
        return judge.rubric_judge(
            self.deliverable_text(run_dir),
            task.instruction,
            [str(c) for c in criteria],
            model=config.JUDGE_MODEL,
            claude_bin=config.CLAUDE_BIN,
        )
