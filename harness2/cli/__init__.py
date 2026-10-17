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

"""Command-line adaptation, judging, and reporting: the `harness2` console script."""

from __future__ import annotations

import argparse
import sys

from .. import __version__, config
from . import check, demo, export, judge, library, render, report, run, task

COMMANDS = (run, judge, report, demo, check, task, export, library, render)

DESCRIPTION = """\
Harness² for Claude Code: improve the harness (CLAUDE.md, skills, subagents, scripts,
hooks) around a fixed model on your own tasks.

Start with `harness2 demo` (no model calls), then `harness2 check`, then
`harness2 run --k 1 --max-tasks 1 --dry-run`.
"""


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="harness2",
        description=DESCRIPTION,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p.add_argument("--version", action="version", version=f"harness2 {__version__}")
    sub = p.add_subparsers(dest="command", metavar="COMMAND")
    sub.required = True
    for module in COMMANDS:
        module.add_parser(sub)
    return p


def main(argv: list[str] | None = None) -> int:
    """Parse and dispatch; print a misconfiguration as one `error:` line and return 1."""
    args = build_parser().parse_args(argv)
    try:
        return int(args.func(args) or 0)
    except (
        config.ConfigError,
        ValueError,
        FileNotFoundError,
        FileExistsError,
        RuntimeError,
    ) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print("interrupted", file=sys.stderr)
        return 130
