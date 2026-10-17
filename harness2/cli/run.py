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

"""Run one adaptation sweep: every task of a split, domains parallel, tasks in stream order.

Costs real model usage: each task is R=4k Claude Code executor runs and P=3k improver
sessions (plus a selector session when k>1 and a reflection session). `--dry-run` prints the
exact (domain, task) stream and namespaces without calling Claude Code or writing anything;
start there, e.g. `harness2 run --k 1 --max-tasks 1 --dry-run`.
"""

from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path

from .. import config
from . import _common as C


def add_parser(subparsers) -> argparse.ArgumentParser:
    p = subparsers.add_parser(
        "run",
        help="run an adaptation sweep over a tasks directory",
        description=__doc__,
        formatter_class=C.HelpFormatter,
    )
    C.add_tasks_arg(p)
    C.add_runs_arg(p)
    C.add_k_arg(p)
    C.add_modes_arg(p)
    p.add_argument(
        "--workers", type=int, default=1, metavar="N", help="domains to run concurrently"
    )
    p.add_argument("--only-domains", default="", metavar="a,b", help="comma list of domains to run")
    p.add_argument(
        "--only-tasks", default="", metavar="id,id", help="comma list of task ids to run"
    )
    p.add_argument(
        "--max-tasks",
        type=int,
        default=None,
        metavar="N",
        help="per-domain cap on the stream (the first N tasks of every domain)",
    )
    C.add_seed_arg(p)
    p.add_argument(
        "--split",
        choices=("all", "train", "test"),
        default="all",
        help="which tasks of --split-file to run",
    )
    p.add_argument(
        "--split-file",
        type=Path,
        default=None,
        metavar="F",
        help="split file from `harness2 task split`",
    )
    p.add_argument(
        "--base-harness",
        type=Path,
        default=None,
        metavar="DIR",
        help="harness tree to start from (default: the empty v0 shipped with harness2)",
    )
    p.add_argument(
        "--tag", default="", metavar="T", help="optional namespace suffix for a separate run"
    )
    p.add_argument(
        "--dry-run",
        action="store_true",
        help="print the stream order, namespaces and library root, then exit; no model calls",
    )
    p.set_defaults(func=run)
    return p


def _stream_for(adapter, args) -> tuple[dict[str, list[str]], list[tuple[str, str]]]:
    from ..core import schedule

    by_domain = schedule.task_stream(
        adapter,
        split=args.split,
        seed=args.seed,
        only_domains=C.csv_list(args.only_domains),
        only_tasks=C.csv_list(args.only_tasks),
        max_tasks=args.max_tasks,
    )
    return by_domain, [(domain, task) for domain, tasks in by_domain.items() for task in tasks]


def _print_stream(adapter, args, modes) -> int:
    by_domain, stream = _stream_for(adapter, args)
    runs = Path(adapter.spec.runs_root)
    print(
        f"# {adapter.spec.name}-{adapter.substrate.name} split={args.split} "
        f"seed={args.seed} k={args.k} modes={','.join(C.mode_label(m) for m in modes)}"
    )
    for m in modes:
        print(
            f"#   namespace[{C.mode_label(m)}]: "
            f"{C.namespace(m, args.k, seed=args.seed, tag=args.tag)}"
        )
    print(
        f"#   summaries under: {C.runs_root_for(runs)}/"
        f"{C.summary_namespace(args.k, seed=args.seed, tag=args.tag)}"
    )
    print(f"#   rollouts under:  {runs / 'rollouts'}")
    print(f"#   library root:    {C.library_root_for(runs, args.k, seed=args.seed, tag=args.tag)}")
    print(f"#   base harness:    {adapter.base_harness()}")
    print(f"#   domains parallel: {args.workers}")
    for i, (domain, task) in enumerate(stream, 1):
        print(f"{i:>4}  {domain:<28} {task}")
    print("# domains of this split, with task counts (the legal --only-domains names):")
    for d, tasks in by_domain.items():
        print(f"{len(tasks):>4}  {d}")
    cap = f", max {args.max_tasks} per domain" if args.max_tasks is not None else ""
    k, n_modes = args.k, len(modes)
    print(
        f"# {len(stream)} tasks over {len(by_domain)} domains "
        f"(domains parallel, tasks within a domain sequential{cap})"
    )
    print(
        f"# per task: {k + 3 * k * n_modes} Claude Code executor runs and "
        f"{3 * k * n_modes} improver sessions, plus selection/reflection sessions"
    )
    return 0 if stream else 1


def run(args: argparse.Namespace) -> int:
    if args.k < 1 or args.workers < 1:
        raise ValueError("--k and --workers must be positive")
    modes = C.parse_modes(args.modes)
    from ..core import schedule

    schedule.validate_tag(args.tag)
    adapter = C.make_adapter(
        args.tasks, args.runs, base_harness=args.base_harness, split_file=args.split_file
    )
    if args.dry_run:
        return _print_stream(adapter, args, modes)

    from ..core import tree as HT
    from ..core.proposer import Proposer

    _stream_for(adapter, args)
    config.validate()
    surface = adapter.substrate.surface()
    try:
        HT.validate(surface, HT.read_tree(surface, adapter.base_harness()))
    except ValueError as exc:
        raise ValueError(f"base harness {adapter.base_harness()} is invalid: {exc}") from None
    adapter.substrate.preflight()

    runs = Path(adapter.spec.runs_root)
    runs_root = C.runs_root_for(runs)
    library_root = C.library_root_for(runs, args.k, seed=args.seed, tag=args.tag)
    summary_ns = C.summary_namespace(args.k, seed=args.seed, tag=args.tag)
    _check_identity(adapter, args, runs_root / summary_ns)
    prop = Proposer(adapter.spec.channels["proposer"], audit_dir=runs_root / "_audit" / summary_ns)

    only = C.csv_list(args.only_domains)
    print(
        f"sweep {summary_ns}: {args.workers} domain(s) in parallel, tasks within a domain in "
        f"stream order",
        flush=True,
    )
    rep = schedule.run_sweep(
        args.k,
        adapter=adapter,
        prop=prop,
        runs_root=runs_root,
        library_root=library_root,
        split=args.split,
        modes=modes,
        seed=args.seed,
        tag=args.tag,
        domain_parallel=args.workers,
        only_domains=only,
        max_tasks=args.max_tasks,
        only_tasks=C.csv_list(args.only_tasks),
        pool_policy="finals",
    )

    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    rec = runs_root / "_sweeps" / f"{summary_ns}-{stamp}.json"
    rec.parent.mkdir(parents=True, exist_ok=True)
    rec.write_text(json.dumps(rep, indent=2, default=str), encoding="utf-8")

    done = sum(int(d.get("done", 0)) for d in rep["domains"])
    failed = sum(int(d.get("failed", 0)) for d in rep["domains"])
    dead = [d["domain"] for d in rep["domains"] if d.get("error")]
    if not rep["domains"]:
        print(
            f"\nsweep {summary_ns}: zero tasks scheduled (split={args.split!r}, "
            f"only_domains={only!r}, max_tasks={args.max_tasks!r}); refusing to report success"
        )
        print(f"record: {rec}")
        return 1
    print(
        f"\nsweep {summary_ns}: {done} tasks ok, {failed} failed, "
        f"{len(dead)} domain(s) died{': ' + ', '.join(dead) if dead else ''}"
    )
    print(f"record: {rec}")
    print(
        f"next:   harness2 judge --tasks {adapter.tasks_root} --runs {runs} --k {args.k}"
        + C.flags_hint(seed=args.seed, tag=args.tag)
        + f" --mode {args.modes}"
    )
    if dead:
        return 1
    return 2 if failed else 0


def _check_identity(adapter, args, namespace_dir: Path) -> None:
    """Prevent a resumed run or library from silently mixing models, tasks or base harness."""
    from ..core import runmark

    identity = {
        "method_version": config.METHOD_VERSION,
        "executor": config.EXECUTOR_MODEL,
        "executor_effort": config.EXECUTOR_EFFORT,
        "improver": config.IMPROVER_MODEL,
        "improver_effort": config.IMPROVER_EFFORT,
        "judge": config.JUDGE_MODEL,
        "split": args.split,
        "tasks": _stream_for(adapter, args)[1],
        "max_tasks": args.max_tasks,
        "domains": sorted(C.csv_list(args.only_domains)),
        "seed": args.seed,
        "base_harness": runmark.fingerprint(adapter.base_harness()),
    }
    namespace_dir.mkdir(parents=True, exist_ok=True)
    path = namespace_dir / "config.json"
    if path.is_file():
        try:
            old = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            raise ValueError(f"unreadable run identity {path}: {exc}") from None
        if old != json.loads(json.dumps(identity)):
            changed = sorted(
                key for key in set(old) | set(identity) if old.get(key) != identity.get(key)
            )
            raise ValueError(
                f"{namespace_dir} belongs to a run with different {', '.join(changed)}. "
                f"Use a new --runs directory (or --tag) for a new experiment."
            )
    else:
        path.write_text(json.dumps(identity, indent=2) + "\n", encoding="utf-8")
