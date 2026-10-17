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

"""Run the real recursion and library workflow with local, deterministic editors.

No model is called and nothing is billed: rollouts run the harness's scripts/summarize.py
with this Python, and the improver/selector replies are scripted. The result is the same
on-disk layout a real run writes, under --output, plus a demo.json summary.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from . import _common as C


def add_parser(subparsers) -> argparse.ArgumentParser:
    p = subparsers.add_parser(
        "demo",
        help="run the whole method on two toy tasks with no model calls",
        description=__doc__,
        formatter_class=C.HelpFormatter,
    )
    p.add_argument(
        "--output", type=Path, default=Path("runs/demo"), metavar="DIR", help="output directory"
    )
    C.add_k_arg(p, required=False, default=2)
    C.add_modes_arg(p)
    p.set_defaults(func=run)
    return p


def run(args: argparse.Namespace) -> int:
    from ..core.schedule import run_domain
    from ..demo import DemoAdapter, DemoProposer

    if args.k < 1:
        raise ValueError("--k must be positive")
    modes = C.parse_modes(args.modes)
    root = C.path(args.output)
    adapter = DemoAdapter(root)
    proposer = DemoProposer(adapter.spec.channels["proposer"])
    result = run_domain(
        "accounting",
        adapter.tasks(),
        args.k,
        adapter=adapter,
        prop=proposer,
        runs_root=adapter.spec.runs_root,
        library_root=root / "library" / f"k{args.k}",
        modes=modes,
    )
    result["execution_count"] = adapter.execution_count
    result["editor_calls"] = len(proposer._calls)
    report = root / "demo.json"
    report.write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
    print(f"Local demo: {result['done']} tasks completed; {result['failed']} failed. {report}")
    if not result["failed"]:
        print(
            f"Adopted harness: harness2 export --runs {adapter.spec.runs_root} --task ledger-1 "
            f"--k {args.k} --mode {C.mode_label(modes[0])} --dest <project>"
        )
    return int(result["failed"] > 0)
