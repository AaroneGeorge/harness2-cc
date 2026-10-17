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

"""Check the setup before a real run: configuration, the Claude Code CLI, tasks, base harness.

Model access is not exercised (only `claude --version` runs); the first real run is the first
model call.
"""

from __future__ import annotations

import argparse
import subprocess
from collections import Counter

from .. import config
from . import _common as C


def add_parser(subparsers) -> argparse.ArgumentParser:
    p = subparsers.add_parser(
        "check",
        help="preflight: configuration, claude CLI, tasks, base harness",
        description=__doc__,
        formatter_class=C.HelpFormatter,
    )
    C.add_tasks_arg(p)
    p.set_defaults(func=run)
    return p


def _claude_version() -> str:
    try:
        p = subprocess.run(
            [config.CLAUDE_BIN, "--version"], capture_output=True, text=True, timeout=60
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise RuntimeError(f"`{config.CLAUDE_BIN} --version` failed: {exc}") from None
    if p.returncode != 0:
        detail = (p.stderr or p.stdout or "").strip().splitlines()[-1:] or ["no output"]
        raise RuntimeError(
            f"`{config.CLAUDE_BIN} --version` exited {p.returncode}: {detail[0][:200]}"
        )
    return (p.stdout or "").strip().splitlines()[0] if (p.stdout or "").strip() else "(unknown)"


def run(args: argparse.Namespace) -> int:
    from .. import tasks as T
    from ..core import tree as HT
    from ..substrate.surface import CLAUDE_CODE_SURFACE

    problems: list[str] = []
    print("configuration")
    for name in (
        "EXECUTOR_MODEL",
        "EXECUTOR_EFFORT",
        "IMPROVER_MODEL",
        "IMPROVER_EFFORT",
        "IMPROVER_MAX_BUDGET_USD",
        "JUDGE_MODEL",
        "RUNS_DIR",
        "TASKS_DIR",
        "CLAUDE_BIN",
        "PHASE_WIDTH",
        "PROPOSER_CONCURRENCY",
        "TRANSPORT_ATTEMPTS",
    ):
        value = getattr(config, name)
        print(f"  HARNESS2_{name:<24} {value if value != '' else '(Claude Code default)'}")
    try:
        config.validate()
    except config.ConfigError as exc:
        problems.append(str(exc))

    try:
        print(f"claude CLI: {_claude_version()} ({config.CLAUDE_BIN})")
    except RuntimeError as exc:
        problems.append(str(exc))

    root = C.path(args.tasks)
    try:
        found = T.discover(root)
    except FileNotFoundError:
        # Not a setup problem: the Quick start runs `check` before `task new`.
        found = {}
        print(
            f"tasks: none yet under {root}; create one with "
            f"`harness2 task new <domain> <name>` (or pass --tasks DIR)"
        )
    if found:
        bad = 0
        for task_id in found:
            try:
                T.load(root, task_id)
            except (OSError, ValueError) as exc:
                bad += 1
                problems.append(f"task {task_id}: {exc}")
        counts = Counter(found.values())
        doms = ", ".join(f"{d} ({n})" for d, n in sorted(counts.items()))
        invalid = f"; {bad} invalid" if bad else ""
        print(f"tasks: {len(found)} under {root}; domains: {doms}{invalid}")

    base = config.base_harness_dir()
    try:
        census = HT.validate(CLAUDE_CODE_SURFACE, HT.read_tree(CLAUDE_CODE_SURFACE, base))
        shown = ", ".join(f"{k}={v}" for k, v in census.items())
        print(f"base harness: {base} ({shown})")
    except ValueError as exc:
        problems.append(f"base harness {base}: {exc}")

    for problem in problems:
        print(f"FAIL: {problem}")
    if problems:
        print(f"Setup incomplete: {len(problems)} issue(s).")
        return 1
    if found:
        print("Ready. Next: harness2 run --k 1 --max-tasks 1 --dry-run")
    else:
        print("Ready. Next: harness2 task new <domain> <name>")
    return 0
