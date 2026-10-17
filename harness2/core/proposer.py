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

"""Improver sessions with file access in an isolated context workspace.

Every session is one `claude -p --output-format json` run whose working directory is a fresh
temp dir holding `context/`. The reply is the final message; the session edits files only with
Read/Edit/Write/Glob/Grep. Prompts use stdin, and empty replies raise an error.

The small CLI helpers here (`child_env`, `supported_flags`, `build_command`, `run_cli`) are
shared with the executor in `harness2.substrate.claude_code`.
"""

from __future__ import annotations

import contextlib
import hashlib
import json
import os
import re
import shutil
import signal
import subprocess
import tempfile
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path

from .. import config
from .spec import ChannelSpec

SYSTEM_ARG_MAX = 120_000

# Seconds to wait before retry n (times n-1), as upstream. Tests set it to 0.
RETRY_BACKOFF_S = 10

MAX_CONCURRENCY = int(config.PROPOSER_CONCURRENCY)
_SEM = threading.BoundedSemaphore(MAX_CONCURRENCY) if MAX_CONCURRENCY > 0 else None

# Removed from the child environment so a nested `claude -p` is allowed when harness2 itself
# runs inside a Claude Code session. Nothing else is stripped or added: auth is the user's.
NESTED_ENV = ("CLAUDECODE", "CLAUDE_CODE_ENTRYPOINT")

IMPROVER_TOOLS = "Read,Edit,Write,Glob,Grep"
# Used only when the installed CLI has no --tools flag.
FALLBACK_DISALLOWED = ("Bash", "WebFetch", "WebSearch", "Agent")


def child_env() -> dict[str, str]:
    """A copy of os.environ for a `claude` child, minus the nested-session markers."""
    return {k: v for k, v in os.environ.items() if k not in NESTED_ENV}


def resolve_bin(claude_bin: str) -> str:
    """The absolute path of the CLI when it is on PATH, else the name as given."""
    return shutil.which(claude_bin) or claude_bin


_FLAG_RX = re.compile(r"(?<![\w-])(--[A-Za-z][\w-]*)")
_FLAGS_CACHE: dict[tuple[str, int], frozenset[str] | None] = {}
_FLAGS_LOCK = threading.Lock()


def supported_flags(claude_bin: str) -> frozenset[str] | None:
    """Long flags the installed CLI lists in `--help`, or None when that cannot be read
    (then callers keep every flag rather than guess). Cached per binary path and mtime.
    """
    path = resolve_bin(claude_bin)
    try:
        key = (path, os.stat(path).st_mtime_ns)
    except OSError:
        key = (path, 0)
    with _FLAGS_LOCK:
        if key in _FLAGS_CACHE:
            return _FLAGS_CACHE[key]
    try:
        p = subprocess.run(
            [path, "--help"],
            env=child_env(),
            text=True,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=60,
        )
        found = frozenset(_FLAG_RX.findall(p.stdout or "")) if p.returncode == 0 else None
    except (OSError, subprocess.SubprocessError):
        found = None
    if found is not None and "--output-format" not in found:
        found = None
    with _FLAGS_LOCK:
        _FLAGS_CACHE[key] = found
    return found


def build_command(
    claude_bin: str,
    parts: list[tuple[str, ...]],
    *,
    optional: frozenset[str] | set[str],
    flags: frozenset[str] | None,
) -> tuple[list[str], list[str]]:
    """`[bin, *flag, *values...]` from (flag, *values) parts. A flag named in `optional` that
    the installed CLI does not list is dropped (with its values); returns (argv, dropped).
    """
    cmd = [resolve_bin(claude_bin)]
    dropped: list[str] = []
    for flag, *values in parts:
        if flags is not None and flag in optional and flag not in flags:
            dropped.append(flag)
            continue
        cmd += [flag, *values]
    return cmd, dropped


def _kill_group(p: subprocess.Popen) -> None:
    """Kill the child's whole process group (it was started in its own session)."""
    with contextlib.suppress(ProcessLookupError, PermissionError, OSError):
        os.killpg(p.pid, signal.SIGKILL)
    with contextlib.suppress(OSError):
        p.kill()


def run_cli(
    cmd: list[str],
    *,
    cwd: Path,
    stdin: str,
    timeout: float,
    stdout=subprocess.PIPE,
    stderr=subprocess.PIPE,
) -> tuple[int, str, str]:
    """Run `cmd` with `stdin` in its own process group; (returncode, stdout, stderr).

    Output sent to a file handle comes back as "". On timeout the whole group is killed (a
    hung CLI cannot leak children) and `subprocess.TimeoutExpired` is raised.
    """
    p = subprocess.Popen(
        cmd,
        cwd=cwd,
        env=child_env(),
        stdin=subprocess.PIPE,
        stdout=stdout,
        stderr=stderr,
        start_new_session=True,
    )
    try:
        out, err = p.communicate(input=stdin.encode("utf-8"), timeout=timeout)
    except subprocess.TimeoutExpired:
        _kill_group(p)
        with contextlib.suppress(Exception):
            p.communicate(timeout=10)
        raise
    except BaseException:
        _kill_group(p)
        raise
    return p.returncode, _dec(out), _dec(err)


def _dec(b: bytes | None) -> str:
    return b.decode("utf-8", errors="replace") if isinstance(b, bytes) else ""


class ProposerError(RuntimeError):
    """A call did not produce a usable reply. Never swallowed into an empty string."""

    def __init__(self, message: str, *, attempts: int = 0, cost_usd: float = 0.0):
        super().__init__(message)
        self.attempts = attempts
        self.cost_usd = cost_usd


@dataclass
class Reply:
    """One session: what came back, what it cost, and where the audit trail is."""

    label: str
    text: str
    ok: bool
    cost_usd: float = 0.0
    wall_s: float = 0.0
    attempts: int = 0
    cached: bool = False
    channel: str = ""
    error: str = ""
    audit: Path | None = None
    workspace: Path | None = None

    def __bool__(self) -> bool:
        return self.ok


@dataclass
class Proposer:
    """Runs agentic sessions on one channel, with an audit trail and a cache."""

    spec: ChannelSpec
    audit_dir: Path | None = None
    attempts: int = 2
    claude_bin: str = field(default_factory=lambda: config.CLAUDE_BIN)
    _calls: list[Reply] = field(default_factory=list)

    def __post_init__(self) -> None:
        if not self.spec.model.strip():
            raise ValueError("Set HARNESS2_IMPROVER_MODEL before starting model sessions")

    def env(self) -> dict[str, str]:
        return child_env()

    def call(
        self,
        mission: str,
        context_dir: Path | str | None,
        label: str,
        *,
        system: str = "",
        timeout: int | None = None,
        max_attempts: int | None = None,
        harvest: str | None = None,
    ) -> Reply:
        """Run one session. Raises ProposerError if no usable reply after `attempts`.

        `harvest` names a subpath of the session's working directory to copy out before that
        directory is destroyed -- e.g. "context/harness", which is how an edited tree comes back.
        """

        cache_key = _call_fingerprint(label, mission, system, context_dir)
        cached = self._cached(label, cache_key, require_workspace=bool(harvest))
        if cached is not None:
            self._calls.append(cached)
            return cached

        wall = int(timeout or self.spec.timeout)
        tries = max(1, int(self.attempts if max_attempts is None else max_attempts))
        last = ""
        spent_usd = 0.0
        t0 = time.time()
        for attempt in range(1, tries + 1):
            if attempt > 1 and RETRY_BACKOFF_S:
                time.sleep(RETRY_BACKOFF_S * (attempt - 1))
            tmp = Path(tempfile.mkdtemp(prefix="harness2-prop-"))
            try:
                if context_dir and Path(context_dir).is_dir():
                    shutil.copytree(context_dir, tmp / "context", dirs_exist_ok=True)
                with _SEM if _SEM is not None else contextlib.nullcontext():
                    text, cost, err = self._claude_cli(tmp, mission, system, wall)
                spent_usd += cost
                if not text.strip():
                    last = err or "empty reply"
                    continue
                r = Reply(
                    label=label,
                    text=text,
                    ok=True,
                    cost_usd=spent_usd,
                    wall_s=round(time.time() - t0, 1),
                    attempts=attempt,
                    channel=self.spec.kind,
                )
                if harvest:
                    r.workspace = self._harvest(tmp, harvest, label, cache_key)
                    if r.workspace is None:
                        _drop_workspace(self._paths(label, cache_key))
                self._write_audit(r, mission, system, cache_key)
                self._calls.append(r)
                return r
            except subprocess.TimeoutExpired:
                last = f"timeout after {wall}s"
            except Exception as exc:
                last = f"{type(exc).__name__}: {exc}"
            finally:
                shutil.rmtree(tmp, ignore_errors=True)

        r = Reply(
            label=label,
            text="",
            ok=False,
            wall_s=round(time.time() - t0, 1),
            attempts=tries,
            channel=self.spec.kind,
            error=last,
            cost_usd=spent_usd,
        )
        self._calls.append(r)
        self._write_audit(r, mission, system, cache_key)
        raise ProposerError(
            f"[{label}] {self.spec.kind}/{self.spec.model}: {last}",
            attempts=tries,
            cost_usd=spent_usd,
        )

    def command(self, system: str = "") -> tuple[list[str], str, list[str]]:
        """(argv, stdin prefix, dropped flags) for one session; the mission is appended to the
        prefix and sent on stdin.
        """
        flags = supported_flags(self.claude_bin)
        parts: list[tuple[str, ...]] = [
            ("-p",),
            ("--output-format", "json"),
            ("--model", self.spec.model),
        ]
        if self.spec.effort:
            parts.append(("--effort", self.spec.effort))
        parts.append(("--permission-mode", "bypassPermissions"))
        if flags is None or "--tools" in flags:
            parts.append(("--tools", IMPROVER_TOOLS))
        else:
            parts.append(("--disallowedTools", *FALLBACK_DISALLOWED))
        parts += [
            ("--setting-sources", "project"),
            ("--no-session-persistence",),
        ]
        if config.IMPROVER_MAX_BUDGET_USD > 0:
            parts.append(("--max-budget-usd", str(config.IMPROVER_MAX_BUDGET_USD)))
        prefix = ""
        if system:
            if len(system) > SYSTEM_ARG_MAX:
                prefix = f"# SYSTEM INSTRUCTIONS\n\n{system}\n\n# END\n\n"
                parts.append(
                    (
                        "--append-system-prompt",
                        "Follow the SYSTEM INSTRUCTIONS block at the top of the message.",
                    )
                )
            else:
                parts.append(("--append-system-prompt", system))
        cmd, dropped = build_command(
            self.claude_bin,
            parts,
            optional={
                "--effort",
                "--setting-sources",
                "--no-session-persistence",
                "--max-budget-usd",
            },
            flags=flags,
        )
        return cmd, prefix, dropped

    def _claude_cli(
        self, cwd: Path, mission: str, system: str, wall: int
    ) -> tuple[str, float, str]:
        cmd, prefix, _ = self.command(system)
        rc, out, stderr = run_cli(cmd, cwd=cwd, stdin=prefix + mission, timeout=wall)
        payload = _parse_json(out)
        text = str(payload.get("result") or payload.get("text") or "")
        try:
            cost = float(payload.get("total_cost_usd") or 0.0)
        except (TypeError, ValueError):
            cost = 0.0
        err = ""
        if payload.get("is_error") or payload.get("subtype") not in (None, "success"):
            detail = payload.get("errors") or text[:200]
            err = f"CLI error: {payload.get('subtype')} {str(detail)[:300]}"
            if payload.get("subtype") == "error_max_budget_usd":
                # Claude Code checks the cap between turns, so a session can overshoot it; the
                # session's edits are discarded with it.
                err += (
                    f" (session spent ${cost:.2f} against the "
                    f"HARNESS2_IMPROVER_MAX_BUDGET_USD={config.IMPROVER_MAX_BUDGET_USD:g} cap)"
                )
            text = ""
        elif rc != 0 and not text.strip():
            err = f"rc={rc}: {(stderr or '')[-300:]}"
        return text, cost, err

    def _harvest(self, tmp: Path, sub: str, label: str, cache_key: str = "") -> Path | None:
        """Copy `tmp/<sub>` somewhere durable before the session directory is removed."""
        src = tmp / sub
        if not src.is_dir():
            return None
        dest = Path(self.audit_dir or tempfile.mkdtemp(prefix="harness2-harvest-"))
        safe = re.sub(r"[^A-Za-z0-9_.-]+", "_", label)
        if cache_key:
            safe = f"{safe}.{cache_key}"
        dest = dest / f"{safe}.workspace"
        shutil.rmtree(dest, ignore_errors=True)
        shutil.copytree(src, dest)
        return dest

    def _paths(self, label: str, cache_key: str = "") -> dict[str, Path] | None:
        if not self.audit_dir:
            return None
        d = Path(self.audit_dir)
        d.mkdir(parents=True, exist_ok=True)
        safe = re.sub(r"[^A-Za-z0-9_.-]+", "_", label)
        if cache_key:
            safe = f"{safe}.{cache_key}"
        return {k: d / f"{safe}.{k}.md" for k in ("mission", "system", "response")} | {
            "meta": d / f"{safe}.meta.json",
            "workspace": d / f"{safe}.workspace",
        }

    def _cached(
        self, label: str, cache_key: str = "", *, require_workspace: bool = False
    ) -> Reply | None:
        p = self._paths(label, cache_key)
        if not p or not p["response"].is_file():
            return None
        if require_workspace and not p["workspace"].is_dir():
            return None
        txt = p["response"].read_text(encoding="utf-8", errors="replace")
        if not txt.strip():
            return None
        return Reply(
            label=label,
            text=txt,
            ok=True,
            cost_usd=0.0,
            cached=True,
            channel=self.spec.kind,
            audit=p["response"],
            workspace=p["workspace"] if p["workspace"].is_dir() else None,
        )

    def _write_audit(self, r: Reply, mission: str, system: str, cache_key: str = "") -> None:
        p = self._paths(r.label, cache_key)
        if not p:
            return
        p["mission"].write_text(mission, encoding="utf-8")
        if system:
            p["system"].write_text(system, encoding="utf-8")
        p["response"].write_text(r.text, encoding="utf-8")
        p["meta"].write_text(
            json.dumps(
                {
                    "label": r.label,
                    "ok": r.ok,
                    "channel": r.channel,
                    "model": self.spec.model,
                    "effort": self.spec.effort,
                    "attempts": r.attempts,
                    "wall_s": r.wall_s,
                    "cost_usd": r.cost_usd,
                    "error": r.error,
                },
                indent=2,
            ),
            encoding="utf-8",
        )
        r.audit = p["response"]


def _drop_workspace(paths: dict[str, Path] | None) -> None:
    """Remove a stale harvested workspace so a later cache read cannot serve it."""
    if paths:
        shutil.rmtree(paths["workspace"], ignore_errors=True)


def _parse_json(stdout: str) -> dict:
    """The claude CLI's one JSON object, tolerating banners and trailing noise around it."""
    s = (stdout or "").strip()
    if not s:
        return {}
    try:
        v = json.loads(s)
        return v if isinstance(v, dict) else {"result": s}
    except json.JSONDecodeError:
        for line in reversed(s.splitlines()):
            line = line.strip()
            if line.startswith("{") and line.endswith("}"):
                try:
                    v = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if isinstance(v, dict):
                    return v
        i, j = s.find("{"), s.rfind("}")
        if 0 <= i < j:
            try:
                v = json.loads(s[i : j + 1])
                if isinstance(v, dict):
                    return v
            except json.JSONDecodeError:
                pass
    return {"result": s}


def _call_fingerprint(label: str, mission: str, system: str, context_dir: Path | str | None) -> str:
    """Content-address one call, including its evidence and its mode/task workspace path.

    The resolved path is part of the key: the two modes can have byte-identical k=1 evidence
    and must still be independent draws.
    """
    h = hashlib.sha256()
    for value in (label, mission, system):
        h.update(value.encode("utf-8", errors="replace"))
        h.update(b"\0")
    if context_dir:
        root = Path(context_dir)
        h.update(str(root.resolve()).encode("utf-8", errors="replace"))
        h.update(b"\0")
        if root.is_dir():
            for p in sorted(root.rglob("*")):
                if not p.is_file() or "__pycache__" in p.parts:
                    continue
                h.update(str(p.relative_to(root)).encode("utf-8", errors="replace"))
                h.update(b"\0")
                try:
                    with p.open("rb") as fh:
                        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
                            h.update(chunk)
                except OSError as exc:
                    h.update(f"<unreadable:{type(exc).__name__}:{exc}>".encode())
                h.update(b"\0")
    return h.hexdigest()[:16]
