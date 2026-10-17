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

"""Shared plumbing for the CLI: adapter construction and the on-disk conventions.

The conventions are defined here once, because `run` writes them and `judge`, `report`,
`export` and `library` read them back:

    <runs>/adapt/<summary ns>/<task>/summary.json     both modes' records
    <runs>/adapt/<mode ns>/<task>/harnesses/<label>/  adopted and candidate harnesses
    <runs>/rollouts/<run_id>/                         one rollout
    <runs>/library/local/k<k>-cc[-s<seed>][-<tag>]/<mode>/<domain>/
"""

from __future__ import annotations

import argparse
from pathlib import Path

from .. import config

BENCH = "local"
SUBSTRATE = "cc"

MODES_DEFAULT = "parallel"
MODES_HELP = (
    "parallel | sequential | both (agg / seq accepted: the on-disk tokens; a comma list "
    "such as 'parallel,sequential' also works)"
)


class HelpFormatter(argparse.RawDescriptionHelpFormatter):
    """Meaningful defaults in the help text, module docstring kept verbatim as description."""

    def _get_help_string(self, action: argparse.Action) -> str:
        text = action.help or ""
        default = action.default
        if (
            "%(default)" in text
            or default in (None, "", False, argparse.SUPPRESS)
            or action.option_strings == []
        ):
            return text
        return f"{text} (default: %(default)s)"


def path(raw: str | Path) -> Path:
    """A user path, made absolute so it survives being recorded in summaries."""
    return Path(raw).expanduser().resolve()


def add_tasks_arg(p: argparse._ActionsContainer) -> None:
    p.add_argument(
        "--tasks",
        type=Path,
        default=config.TASKS_DIR,
        metavar="DIR",
        help="tasks root: <tasks>/<domain>/<task>/task.json (HARNESS2_TASKS_DIR)",
    )


def add_runs_arg(p: argparse._ActionsContainer) -> None:
    p.add_argument(
        "--runs",
        type=Path,
        default=config.RUNS_DIR,
        metavar="DIR",
        help="output root (HARNESS2_RUNS_DIR)",
    )


def add_modes_arg(p: argparse._ActionsContainer, *, default: str = MODES_DEFAULT) -> None:
    p.add_argument(
        "--mode", "--modes", dest="modes", default=default, metavar="MODE", help=MODES_HELP
    )


def add_k_arg(p: argparse._ActionsContainer, *, required: bool = True, default: int = 1) -> None:
    p.add_argument(
        "--k",
        type=int,
        required=required,
        default=None if required else default,
        metavar="K",
        help="scale: R=4k rollouts, P=3k improver sessions per task",
    )


def add_seed_arg(p: argparse._ActionsContainer) -> None:
    p.add_argument(
        "--seed",
        type=int,
        default=None,
        metavar="N",
        help="stream-order seed; omit for sorted task order",
    )


def add_tag_arg(p: argparse._ActionsContainer) -> None:
    p.add_argument("--tag", default="", metavar="T", help="the --tag the run used, if any")


def flags_hint(*, seed: int | None, tag: str = "") -> str:
    """The ` --seed N --tag T` part of a suggested follow-up command."""
    return (f" --seed {seed}" if seed is not None else "") + (f" --tag {tag}" if tag else "")


def make_adapter(
    tasks_dir: Path | str,
    runs_dir: Path | str,
    *,
    base_harness: Path | str | None = None,
    split_file: Path | str | None = None,
):
    """The one place the CLI turns (--tasks, --runs, --base-harness) into an adapter."""
    from ..adapters.local import LocalTasksAdapter
    from ..core import tree as HT
    from ..substrate.claude_code import ClaudeCodeSubstrate
    from ..substrate.surface import CLAUDE_CODE_SURFACE

    base = path(base_harness) if base_harness is not None else config.base_harness_dir()
    if not HT.is_tree(CLAUDE_CODE_SURFACE, base):
        raise FileNotFoundError(
            f"--base-harness {base} is not a harness tree (it needs CLAUDE.md, guardrails.md, "
            f"MEMORY.md or tool_guidance.md); create one with `harness2 task import`"
        )
    substrate = ClaudeCodeSubstrate(
        model=config.EXECUTOR_MODEL, effort=config.EXECUTOR_EFFORT, claude_bin=config.CLAUDE_BIN
    )
    return LocalTasksAdapter(
        path(tasks_dir),
        substrate=substrate,
        base_harness=base,
        runs_root=path(runs_dir),
        split_file=path(split_file) if split_file is not None else None,
    )


def runs_root_for(runs: Path | str) -> Path:
    """Scheduler state: summaries, harnesses and selections, not the rollout store."""
    return Path(runs) / "adapt"


def library_root_for(runs: Path | str, k: int, *, seed: int | None, tag: str = "") -> Path:
    """Per-(k, seed, tag) experience-library root (the improver's R).

    Separate seeds and tags keep independent task streams from sharing entries.
    """
    from ..core import library as LIB
    from ..core import schedule as SCHED

    SCHED.validate_tag(tag)
    variant = SUBSTRATE
    if seed is not None:
        variant += f"-s{int(seed)}"
    if tag:
        variant += f"-{tag}"
    return LIB.root_for(Path(runs) / "library", benchmark=BENCH, k=k, variant=variant)


def namespace(mode: str, k: int, *, seed: int | None, tag: str = "", bench: str = BENCH) -> str:
    from ..core import schedule as SCHED

    return SCHED.ns(bench, SUBSTRATE, mode, k, seed=seed, tag=tag)


def summary_namespace(k: int, *, seed: int | None, tag: str = "", bench: str = BENCH) -> str:
    """The namespace whose task dirs hold summary.json for BOTH modes: run_task keeps the
    summary in the parallel mode's namespace (on-disk token "agg") whichever modes ran."""
    return namespace("agg", k, seed=seed, tag=tag, bench=bench)


def task_slug(task_id: str) -> str:
    """The task directory name inside a namespace (as schedule.Namespace.task_dir)."""
    return str(task_id).replace("/", "__")


def parse_modes(raw: str) -> tuple[str, ...]:
    """'parallel', 'sequential', 'both' or a comma list -> tokens in MODES order."""
    from ..core import schedule as SCHED

    names = [s.strip() for s in str(raw).split(",") if s.strip()]
    if not names:
        raise ValueError(f"--mode takes parallel | sequential | both, got {raw!r}")
    tokens = set()
    for name in names:
        if name.lower() == "both":
            tokens.update(SCHED.MODES)
            continue
        try:
            tokens.add(SCHED.mode_token(name))
        except ValueError as exc:
            raise ValueError(f"--mode: {exc} (got {raw!r})") from None
    return tuple(t for t in SCHED.MODES if t in tokens)


def mode_label(token: str) -> str:
    """Human name for an on-disk token ('agg' -> 'parallel'); unknown tokens pass through."""
    from ..core import schedule as SCHED

    return SCHED.MODE_NAMES.get(token, token)


def csv_list(raw: str | None) -> tuple[str, ...]:
    return tuple(s.strip() for s in (raw or "").split(",") if s.strip())
