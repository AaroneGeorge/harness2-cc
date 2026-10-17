"""Show the per-domain experience library a run built: each domain's INDEX.md lists the
earlier tasks whose adopted harness (and the lesson behind it) later tasks read.
"""

from __future__ import annotations

import argparse

from . import _common as C


def add_parser(subparsers) -> argparse.ArgumentParser:
    p = subparsers.add_parser(
        "library",
        help="inspect the per-domain experience library",
        description=__doc__,
        formatter_class=C.HelpFormatter,
    )
    sub = p.add_subparsers(dest="library_command", metavar="ACTION")
    sub.required = True
    show = sub.add_parser(
        "show", help="print INDEX.md for one or every domain", formatter_class=C.HelpFormatter
    )
    C.add_runs_arg(show)
    C.add_k_arg(show)
    show.add_argument("--mode", default="parallel", metavar="MODE", help="parallel | sequential")
    show.add_argument(
        "--domain", default=None, metavar="D", help="one domain (default: every domain)"
    )
    C.add_seed_arg(show)
    C.add_tag_arg(show)
    show.set_defaults(func=run_show)
    return p


def run_show(args: argparse.Namespace) -> int:
    from ..core import library as LIB
    from ..core import schedule as SCHED

    mode = SCHED.mode_token(args.mode)
    root = C.library_root_for(C.path(args.runs), args.k, seed=args.seed, tag=args.tag) / mode
    if args.domain:
        dirs = [root / LIB.domain_dir(args.domain)]
    else:
        dirs = sorted(d for d in root.iterdir() if d.is_dir()) if root.is_dir() else []
    shown = 0
    for d in dirs:
        index = d / "INDEX.md"
        if not index.is_file():
            continue
        if shown:
            print()
        print(f"==> {index}")
        print(index.read_text(encoding="utf-8").rstrip())
        shown += 1
    if not shown:
        raise FileNotFoundError(
            f"no library under {root}"
            + (f" for domain {args.domain!r}" if args.domain else "")
            + "; it fills as tasks finish in a real run (check --runs/--k/--mode/--seed/--tag)"
        )
    return 0
