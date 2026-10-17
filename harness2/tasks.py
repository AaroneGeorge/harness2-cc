"""Tasks on disk: `<tasks>/<domain>/<task-dir>/` with a task.json, discovery, loading,
scaffolding and optional train/test split files.
"""

from __future__ import annotations

import fnmatch
import json
import random
import re
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path

from .config import EFFORT_LEVELS

# Hidden from every model context (enforced by core.context.assert_no_leak_paths).
LEAK_GLOBS: tuple[str, ...] = ("rubric.json", "check.py", "judged.json", "*.judged.json")

DEFAULT_DELIVERABLES = ("output/answer.md",)
DEFAULT_TIMEOUT_S = 1800
DEFAULT_MAX_BUDGET_USD = 10.0

_SEGMENT = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.\-]*")


@dataclass
class Task:
    """One task as the executor runs it."""

    id: str
    domain: str
    dir: Path
    instruction: str
    deliverables: list[str] = field(default_factory=lambda: list(DEFAULT_DELIVERABLES))
    timeout_s: int = DEFAULT_TIMEOUT_S
    max_budget_usd: float = DEFAULT_MAX_BUDGET_USD
    effort: str = ""
    workspace: Path | None = None


def is_leak(rel: str, patterns: tuple[str, ...] = LEAK_GLOBS) -> bool:
    """Whether a task-relative path matches a leak glob (same rule as the context builder)."""
    rel = str(rel).replace("\\", "/").lstrip("./")
    return any(
        fnmatch.fnmatch(rel, g.lstrip("./")) or fnmatch.fnmatch(rel, f"*/{g.lstrip('./')}")
        for g in patterns
    )


def _visible(p: Path) -> bool:
    return p.is_dir() and not p.name.startswith((".", "_"))


def discover(tasks_root: Path | str) -> dict[str, str]:
    """Sorted task_id -> domain for every `<domain>/<task>/task.json` under `tasks_root`."""
    root = Path(tasks_root)
    tasks: dict[str, str] = {}
    if root.is_dir():
        for domain_dir in sorted(root.iterdir()):
            if not _visible(domain_dir):
                continue
            for task_dir in sorted(domain_dir.iterdir()):
                if _visible(task_dir) and (task_dir / "task.json").is_file():
                    tasks[f"{domain_dir.name}/{task_dir.name}"] = domain_dir.name
    if not tasks:
        raise FileNotFoundError(
            f"No tasks found under {root} (expected <domain>/<task>/task.json). "
            f"Create one with `harness2 task new <domain> <name> --tasks {root}`."
        )
    return dict(sorted(tasks.items()))


def _split_id(task_id: str) -> tuple[str, str]:
    parts = str(task_id).split("/")
    if len(parts) != 2 or not all(_SEGMENT.fullmatch(p) for p in parts):
        raise ValueError(f"task id must be '<domain>/<task-dir>', got {task_id!r}")
    return parts[0], parts[1]


def _deliverables(raw, where: Path) -> list[str]:
    if raw is None:
        return list(DEFAULT_DELIVERABLES)
    if not isinstance(raw, list) or not raw or not all(isinstance(d, str) for d in raw):
        raise ValueError(f"{where}: `deliverables` must be a non-empty list of relative paths")
    out = []
    for d in raw:
        r = d.strip().replace("\\", "/")
        while r.startswith("./"):
            r = r[2:]
        if not r or r.startswith("/") or ".." in r.split("/") or r.endswith("/"):
            raise ValueError(
                f"{where}: deliverable {d!r} must be a relative file path inside the "
                f"workspace, without '..'"
            )
        out.append(r)
    return out


def load(tasks_root: Path | str, task_id: str) -> Task:
    """Load and validate one task; raise ValueError/FileNotFoundError naming the problem."""
    domain, name = _split_id(task_id)
    d = Path(tasks_root) / domain / name
    tj = d / "task.json"
    if not tj.is_file():
        raise FileNotFoundError(f"{tj} not found; is {task_id!r} a task under {tasks_root}?")
    try:
        meta = json.loads(tj.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ValueError(f"{tj} is not valid JSON: {exc}") from exc
    if not isinstance(meta, dict):
        raise ValueError(f"{tj} must hold a JSON object")

    instruction = meta.get("instruction")
    if instruction is not None and not isinstance(instruction, str):
        raise ValueError(f"{tj}: `instruction` must be a string")
    if not (instruction or "").strip():
        im = d / "instruction.md"
        if not im.is_file():
            raise ValueError(f"{d}: needs an `instruction` in task.json or an instruction.md")
        instruction = im.read_text(encoding="utf-8")
    instruction = instruction.rstrip()
    if not instruction.strip():
        raise ValueError(f"{d}: the task instruction is empty")

    timeout = meta.get("timeout_s", DEFAULT_TIMEOUT_S)
    if isinstance(timeout, bool) or not isinstance(timeout, (int, float)) or timeout <= 0:
        raise ValueError(f"{tj}: `timeout_s` must be a positive number of seconds")
    budget = meta.get("max_budget_usd", DEFAULT_MAX_BUDGET_USD)
    if isinstance(budget, bool) or not isinstance(budget, (int, float)) or budget < 0:
        raise ValueError(
            f"{tj}: `max_budget_usd` must be a non-negative number (0 = no --max-budget-usd cap)"
        )
    effort = meta.get("effort", "") or ""
    if effort != "" and effort not in EFFORT_LEVELS:
        raise ValueError(
            f"{tj}: `effort` {effort!r} must be one of {', '.join(EFFORT_LEVELS)} or empty"
        )

    ws = d / "workspace"
    return Task(
        id=f"{domain}/{name}",
        domain=domain,
        dir=d,
        instruction=instruction,
        deliverables=_deliverables(meta.get("deliverables"), tj),
        timeout_s=int(timeout),
        max_budget_usd=float(budget),
        effort=effort,
        workspace=ws if ws.is_dir() else None,
    )


_INSTRUCTION_PLACEHOLDER = (
    "Describe the task here, as you would to a colleague: what to read, what to produce, and "
    "where to write it.\n\nWrite the result to output/answer.md.\n"
)

_RUBRIC_EXAMPLE = {
    "_comment": (
        "Optional and hidden from the improver. Rename to rubric.json to have the judge grade "
        "the chosen run against these criteria; or add check.py, which takes precedence."
    ),
    "criteria": [
        "The deliverable answers the question the instruction asks.",
        "Every number in the deliverable is traceable to the provided files.",
    ],
}


def scaffold(tasks_root: Path | str, domain: str, name: str, *, instruction: str = "") -> Path:
    """Create `<tasks_root>/<domain>/<name>/` with task.json, instruction.md, an empty
    workspace/ and a rubric.json.example. Refuses to touch an existing task directory.
    """
    for label, v in (("domain", domain), ("name", name)):
        if not _SEGMENT.fullmatch(v or ""):
            raise ValueError(
                f"task {label} {v!r} must start with a letter or digit and contain only "
                f"letters, digits, '.', '_' or '-'"
            )
    d = Path(tasks_root) / domain / name
    if d.exists():
        raise FileExistsError(f"{d} already exists")
    d.mkdir(parents=True)
    (d / "workspace").mkdir()
    meta = {
        "deliverables": list(DEFAULT_DELIVERABLES),
        "timeout_s": DEFAULT_TIMEOUT_S,
        "max_budget_usd": DEFAULT_MAX_BUDGET_USD,
        "effort": "",
    }
    (d / "task.json").write_text(json.dumps(meta, indent=2) + "\n", encoding="utf-8")
    text = instruction.strip() + "\n" if instruction.strip() else _INSTRUCTION_PLACEHOLDER
    (d / "instruction.md").write_text(text, encoding="utf-8")
    (d / "rubric.json.example").write_text(
        json.dumps(_RUBRIC_EXAMPLE, indent=2) + "\n", encoding="utf-8"
    )
    return d


def select(
    tasks: dict[str, str], split: str, path: Path | str | None, *, benchmark: str = "local"
) -> list[str]:
    """Task ids for `split` ("all", "train" or "test"), validated against a split file."""
    if split not in ("all", "train", "test"):
        raise ValueError(f"Unknown split {split!r}; use all, train, or test")
    if path is None:
        if split != "all":
            raise ValueError(
                "--split train/test requires --split-file; create one with "
                "`harness2 task split --seed N --output FILE`, or use --split all"
            )
        return sorted(tasks)
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(data, dict) or data.get("benchmark") != benchmark:
        raise ValueError(f"{path}: expected a split for {benchmark}")
    halves = []
    for name in ("train", "test"):
        ids = data.get(name)
        if not isinstance(ids, list) or any(not isinstance(t, str) for t in ids):
            raise ValueError(f"{path}: {name} must be a list of task IDs")
        if len(set(ids)) != len(ids):
            raise ValueError(f"{path}: duplicate task IDs in {name}")
        halves.append(set(ids))
    if halves[0] & halves[1]:
        raise ValueError(f"{path}: train and test overlap")
    unknown = (halves[0] | halves[1]) - tasks.keys()
    if unknown:
        raise ValueError(f"{path}: task IDs absent from the dataset: {sorted(unknown)[:5]}")
    return sorted(halves[0] | halves[1] if split == "all" else data[split])


def write_split(
    tasks: dict[str, str],
    seed: int,
    output: Path | str,
    train_frac: float = 0.5,
    *,
    benchmark: str = "local",
) -> Path:
    """Write a domain-stratified train/test split file; never overwrites an existing one."""
    if not 0 < train_frac < 1:
        raise ValueError("train fraction must be between 0 and 1")
    by_domain: dict[str, list[str]] = defaultdict(list)
    for task, domain in tasks.items():
        by_domain[domain].append(task)
    train: list[str] = []
    test: list[str] = []
    for domain, ids in sorted(by_domain.items()):
        ids = sorted(ids)
        random.Random(f"{seed}:{domain}").shuffle(ids)
        n = int(len(ids) * train_frac)
        train.extend(ids[:n])
        test.extend(ids[n:])
    data = {
        "benchmark": benchmark,
        "seed": seed,
        "train_fraction": train_frac,
        "train": sorted(train),
        "test": sorted(test),
    }
    out = Path(output)
    out.parent.mkdir(parents=True, exist_ok=True)
    with out.open("x", encoding="utf-8") as f:
        json.dump(data, f, indent=2)
        f.write("\n")
    return out
