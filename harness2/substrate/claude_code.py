# Copyright 2026 Google LLC
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     https://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.
# Modifications Copyright 2026 harness2-claude-code contributors.

"""The Claude Code executor: one `claude -p` rollout per run directory.

A rollout installs a harness into a fresh copy of the task workspace, runs the task prompt
through `claude -p --output-format stream-json`, and keeps the event stream, the final result
event and the declared deliverables. `trajectory()` renders the stream into the shared
[ASSISTANT]/[ACTION]/[OBSERVATION]/[ERROR] grammar the improver reads. The trajectory renderer
follows upstream's `substrate/opencode.py`; the reuse/marker logic follows
`benchmarks/lab.py`.
"""

from __future__ import annotations

import json
import shutil
import subprocess
import time
from collections.abc import Iterator, Sequence
from dataclasses import dataclass, field
from pathlib import Path

from .. import config
from ..core import runmark as RM
from ..core.proposer import build_command, run_cli, supported_flags
from ..core.tree import SurfaceSpec
from ..tasks import Task
from .surface import CLAUDE_CODE_SURFACE, render

STREAM = "stream.jsonl"
STDERR = "stderr.txt"
RESULT = "result.json"
COMMAND = "command.json"
TRAJECTORY = "trajectory.md"
DELIVERABLES = "deliverables"

# Flags the executor can run without; dropped (and recorded) if the installed CLI lacks them.
OPTIONAL_FLAGS = frozenset(
    {"--effort", "--setting-sources", "--no-session-persistence", "--max-budget-usd"}
)

_TRUNC_FMT = "…(+{n} chars — render truncation)"
ARGS_TRUNC = 400
ERROR_TRUNC = 300


def _clip(s, n: int) -> str:
    s = "" if s is None else str(s)
    s = s.replace("\x00", "")
    if n <= 0 or len(s) <= n:
        return s
    return s[:n] + _TRUNC_FMT.format(n=len(s) - n)


def _num(v, default=0):
    return v if isinstance(v, (int, float)) and not isinstance(v, bool) else default


@dataclass
class ClaudeCodeSubstrate:
    """Runs rollouts with the Claude Code CLI and reads them back."""

    name: str = "cc"
    model: str = field(default_factory=lambda: config.EXECUTOR_MODEL)
    effort: str = field(default_factory=lambda: config.EXECUTOR_EFFORT)
    claude_bin: str = field(default_factory=lambda: config.CLAUDE_BIN)

    def surface(self) -> SurfaceSpec:
        return CLAUDE_CODE_SURFACE

    def preflight(self) -> None:
        """Raise ConfigError unless the CLI is on PATH and `claude --version` works."""
        path = shutil.which(self.claude_bin)
        if path is None:
            raise config.ConfigError(
                f"Claude Code CLI {self.claude_bin!r} not found on PATH; install Claude Code and "
                f"log in, or set HARNESS2_CLAUDE_BIN to its path"
            )
        try:
            p = subprocess.run(
                [path, "--version"],
                text=True,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                timeout=60,
            )
        except (OSError, subprocess.SubprocessError) as exc:
            raise config.ConfigError(
                f"`{path} --version` failed ({type(exc).__name__}: {exc}); reinstall Claude Code"
            ) from exc
        if p.returncode != 0:
            raise config.ConfigError(
                f"`{path} --version` exited {p.returncode}: {(p.stderr or '').strip()[-200:]}; "
                f"reinstall Claude Code"
            )

    # ------------------------------------------------------------------ execute

    def command(self, task: Task) -> tuple[list[str], list[str]]:
        """(argv, dropped flags) for one rollout of `task`. The prompt goes on stdin."""
        effort = task.effort or self.effort
        parts: list[tuple[str, ...]] = [
            ("-p",),
            ("--output-format", "stream-json"),
            ("--verbose",),
            ("--permission-mode", "bypassPermissions"),
            ("--model", self.model),
        ]
        if effort:
            parts.append(("--effort", effort))
        parts += [
            ("--setting-sources", "project"),
            ("--no-session-persistence",),
        ]
        if task.max_budget_usd > 0:  # 0 = no cap: do not pass the flag
            parts.append(("--max-budget-usd", str(task.max_budget_usd)))
        return build_command(
            self.claude_bin, parts, optional=OPTIONAL_FLAGS, flags=supported_flags(self.claude_bin)
        )

    def execute(
        self,
        task: Task,
        harness_dir: Path | None,
        run_dir: Path,
        *,
        base_harness: Path,
        timeout: int | None = None,
    ) -> Path:
        """Run one rollout into `run_dir`, idempotently: a run whose marker names the same
        harness fingerprint is reused. A timed-out or failed session is recorded, not raised.
        """
        run_dir = Path(run_dir)
        if RM.is_complete(run_dir, harness_dir=harness_dir) and (run_dir / RESULT).is_file():
            return run_dir
        if run_dir.is_dir() and any(run_dir.iterdir()):
            reason = RM.why_not(run_dir, harness_dir=harness_dir)
            if reason == "reusable":
                reason = f"{RESULT} missing"
            print(f"  [{self.name}] re-rolling {run_dir}: {reason}", flush=True)
            shutil.rmtree(run_dir)
        run_dir.mkdir(parents=True, exist_ok=True)

        ws = run_dir / "workspace"
        if task.workspace is not None and Path(task.workspace).is_dir():
            shutil.copytree(task.workspace, ws, symlinks=True)
        else:
            ws.mkdir()
        render(Path(harness_dir) if harness_dir is not None else Path(base_harness), ws)
        (ws / "output").mkdir(parents=True, exist_ok=True)

        cmd, dropped = self.command(task)
        wall = int(timeout or task.timeout_s)
        (run_dir / COMMAND).write_text(
            json.dumps(
                {"argv": cmd, "cwd": str(ws), "dropped_flags": dropped, "timeout_s": wall},
                indent=2,
            )
            + "\n",
            encoding="utf-8",
        )

        t0 = time.time()
        timed_out = False
        rc: int | None = None
        with (
            (run_dir / STREAM).open("wb") as out,
            (run_dir / STDERR).open("wb") as err,
        ):
            try:
                rc, _, _ = run_cli(
                    cmd, cwd=ws, stdin=task.instruction, timeout=wall, stdout=out, stderr=err
                )
            except subprocess.TimeoutExpired:
                timed_out = True
            except OSError as exc:
                raise RuntimeError(
                    f"could not launch {cmd[0]!r}: {exc}; run `harness2 check`"
                ) from exc
        elapsed_ms = int((time.time() - t0) * 1000)

        n_events, result = 0, None
        for ev in _events(run_dir / STREAM):
            if isinstance(ev, dict):
                n_events += 1
                if ev.get("type") == "result":
                    result = ev
        if result is None:
            if timed_out:
                result = {
                    "type": "result",
                    "subtype": "timeout",
                    "is_error": True,
                    "result": f"killed after the {wall}s wall timeout",
                    "total_cost_usd": 0.0,
                    "num_turns": 0,
                    "duration_ms": elapsed_ms,
                }
            elif n_events == 0:
                # Nothing parseable: the CLI never started a session (bad flag, crash before
                # the stream). Not evidence about the harness, so not cached as a finished run.
                tail = (run_dir / STDERR).read_text(encoding="utf-8", errors="replace")[-300:]
                raise RuntimeError(
                    f"claude exited {rc} with no stream events in {run_dir}: {tail.strip()}"
                )
            else:
                result = {
                    "type": "result",
                    "subtype": "no_result",
                    "is_error": True,
                    "result": f"the stream ended without a result event (exit code {rc})",
                    "total_cost_usd": 0.0,
                    "num_turns": 0,
                    "duration_ms": elapsed_ms,
                }
        (run_dir / RESULT).write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")

        for rel in task.deliverables:
            src = ws / rel
            if src.is_file():
                dest = run_dir / DELIVERABLES / rel
                dest.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(src, dest)

        RM.mark_complete(
            run_dir,
            run_id=str(run_dir),
            harness_dir=harness_dir,
            extra={
                "cost_usd": float(_num(result.get("total_cost_usd"), 0.0)),
                "num_turns": int(_num(result.get("num_turns"), 0)),
                "duration_ms": int(_num(result.get("duration_ms"), elapsed_ms)),
                "subtype": str(result.get("subtype") or ""),
            },
        )
        return run_dir

    # --------------------------------------------------------------- read back

    def trajectory(self, run_dir: Path, *, obs_trunc: int, max_chars: int = 400_000) -> str:
        """The rollout's stream rendered one event per line; also saved as trajectory.md."""
        text = render_stream(
            Path(run_dir) / STREAM,
            obs_trunc=obs_trunc,
            max_chars=max_chars,
            result_file=Path(run_dir) / RESULT,
        )
        try:
            (Path(run_dir) / TRAJECTORY).write_text(text, encoding="utf-8")
        except OSError:
            pass
        return text

    def delivered(self, run_dir: Path, deliverables: Sequence[str]) -> bool:
        if not deliverables:
            return False
        base = Path(run_dir) / DELIVERABLES
        for rel in deliverables:
            p = base / rel
            if not p.is_file() or p.stat().st_size == 0:
                return False
        return True

    def deliverable_files(self, run_dir: Path, deliverables: Sequence[str]) -> dict[str, str]:
        base = Path(run_dir) / DELIVERABLES
        out: dict[str, str] = {}
        for rel in deliverables:
            p = base / rel
            if not p.is_file():
                continue
            try:
                data = p.read_bytes()
            except OSError as exc:
                out[rel] = f"(unreadable file: {type(exc).__name__})"
                continue
            if b"\x00" in data[:8192]:
                out[rel] = f"(binary file, {len(data)} bytes)"
            else:
                out[rel] = data.decode("utf-8", errors="replace")
        return out

    def deliverable_text(self, run_dir: Path, deliverables: Sequence[str]) -> str:
        files = self.deliverable_files(run_dir, deliverables)
        return "\n\n".join(f"# {rel}\n\n{text}" for rel, text in files.items())


# ------------------------------------------------------------------ rendering

_PARSE_ERROR = object()


def _events(path: Path) -> Iterator[object]:
    """Each line of a stream-json file as a parsed value; unparsable lines yield
    (_PARSE_ERROR, line). Reads line by line, never the whole file at once.
    """
    try:
        fh = Path(path).open("r", encoding="utf-8", errors="replace")
    except OSError:
        return
    with fh:
        for raw in fh:
            line = raw.strip()
            if not line:
                continue
            try:
                yield json.loads(line)
            except json.JSONDecodeError:
                yield (_PARSE_ERROR, line)


def _tool_result_text(content) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = []
        for block in content:
            if isinstance(block, dict):
                if block.get("type") == "text":
                    parts.append(str(block.get("text") or ""))
                elif block.get("type") == "image":
                    parts.append("(image)")
                else:
                    parts.append(json.dumps(block, ensure_ascii=False, default=str))
            else:
                parts.append(str(block))
        return "\n".join(parts)
    if content is None:
        return ""
    return json.dumps(content, ensure_ascii=False, default=str)


def _result_lines(ev: dict) -> list[str]:
    lines = []
    subtype = ev.get("subtype") or "unknown"
    if ev.get("is_error"):
        detail = ev.get("errors") or ev.get("result") or ""
        if isinstance(detail, list):
            detail = "; ".join(str(d) for d in detail)
        detail = _clip(detail, ERROR_TRUNC)
        lines.append(f"[ERROR] {subtype}: {detail}" if detail else f"[ERROR] {subtype}")
    cost = _num(ev.get("total_cost_usd"), 0.0)
    dur = _num(ev.get("duration_ms"), 0) / 1000
    lines.append(
        f"[RESULT] subtype={subtype} turns={_num(ev.get('num_turns'), 0)} "
        f"cost_usd={round(float(cost), 4)} duration_s={round(dur, 1)}"
    )
    return lines


def _render_event(ev: dict, obs_trunc: int) -> list[str]:
    kind = ev.get("type")
    msg = ev.get("message") if isinstance(ev.get("message"), dict) else {}
    content = msg.get("content")
    if kind == "assistant" and isinstance(content, list):
        out = []
        for block in content:
            if not isinstance(block, dict):
                continue
            bt = block.get("type")
            if bt == "text":
                txt = block.get("text")
                if isinstance(txt, str) and txt.strip():
                    out.append(f"[ASSISTANT] {_clip(txt, obs_trunc)}")
            elif bt == "thinking":
                txt = block.get("thinking")
                if isinstance(txt, str) and txt.strip():
                    out.append(f"[THINKING] {_clip(txt, obs_trunc)}")
            elif bt in ("tool_use", "server_tool_use"):
                tin = block.get("input")
                args = json.dumps(tin, ensure_ascii=False, default=str) if tin is not None else ""
                out.append(f"[ACTION] {block.get('name') or 'tool'}({_clip(args, ARGS_TRUNC)})")
        return out
    if kind == "user" and isinstance(content, list):
        out = []
        for block in content:
            if isinstance(block, dict) and block.get("type") == "tool_result":
                tag = "OBSERVATION ERROR" if block.get("is_error") else "OBSERVATION"
                out.append(f"[{tag}] {_clip(_tool_result_text(block.get('content')), obs_trunc)}")
        return out
    if kind == "result":
        return _result_lines(ev)
    return []


def render_stream(
    path: Path, *, obs_trunc: int, max_chars: int, result_file: Path | None = None
) -> str:
    """Render a `claude -p --output-format stream-json` file into the shared grammar.

    Unknown events are skipped; an unparsable line is an [ERROR]. When the stream has no
    result event (a killed session), the result recorded in `result_file` closes it instead.
    """
    if not Path(path).is_file():
        return f"(failed to read trajectory: {path} does not exist)"
    kept: list[str] = []
    size = 0  # chars kept, newlines included
    total = 0  # chars the full rendering would have
    saw_result = False

    def add(line: str) -> None:
        # Keep the first `max_chars` of the rendering and only count the rest, so a huge
        # stream never builds a huge string.
        nonlocal size, total
        piece = ("\n" if total else "") + line
        total += len(piece)
        room = max_chars - size
        if room > 0:
            kept.append(piece[:room])
            size += min(room, len(piece))

    for ev in _events(path):
        if isinstance(ev, tuple) and ev and ev[0] is _PARSE_ERROR:
            add(f"[ERROR] unparsable stream line: {_clip(ev[1], ERROR_TRUNC)}")
            continue
        if not isinstance(ev, dict):
            continue
        if ev.get("type") == "result":
            saw_result = True
        for line in _render_event(ev, obs_trunc):
            add(line)
    if not saw_result and result_file is not None:
        try:
            res = json.loads(Path(result_file).read_text(encoding="utf-8"))
        except (OSError, ValueError):
            res = None
        if isinstance(res, dict):
            for line in _result_lines(res):
                add(line)
    if total == 0:
        return "(no parseable events in trajectory)"
    rendered = "".join(kept)
    if total > size:
        rendered += "\n" + _TRUNC_FMT.format(n=total - size)
    return rendered
