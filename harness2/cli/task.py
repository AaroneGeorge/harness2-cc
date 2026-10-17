"""Manage tasks: scaffold a new one, list them, write a train/test split, or import an
existing Claude Code project (CLAUDE.md, .claude/agents, .claude/skills, scripts/, hooks)
as a harness tree to start a run from with `harness2 run --base-harness DIR`.
"""

from __future__ import annotations

import argparse
from pathlib import Path

from .. import config
from . import _common as C


def add_parser(subparsers) -> argparse.ArgumentParser:
    p = subparsers.add_parser(
        "task",
        help="create, list, split or import tasks and harnesses",
        description=__doc__,
        formatter_class=C.HelpFormatter,
    )
    sub = p.add_subparsers(dest="task_command", metavar="ACTION")
    sub.required = True

    new = sub.add_parser(
        "new", help="scaffold <tasks>/<domain>/<name>/", formatter_class=C.HelpFormatter
    )
    new.add_argument("domain")
    new.add_argument("name")
    C.add_tasks_arg(new)
    new.add_argument(
        "--instruction", default="", metavar="TEXT", help="task prompt written to instruction.md"
    )
    new.set_defaults(func=run_new)

    ls = sub.add_parser(
        "list", help="list the tasks under --tasks", formatter_class=C.HelpFormatter
    )
    C.add_tasks_arg(ls)
    ls.set_defaults(func=run_list)

    split = sub.add_parser(
        "split",
        help="write a domain-stratified train/test split file",
        formatter_class=C.HelpFormatter,
    )
    split.add_argument("--seed", type=int, required=True, metavar="N", help="shuffle seed")
    split.add_argument(
        "--output", type=Path, required=True, metavar="F", help="split file to create"
    )
    split.add_argument(
        "--train-frac", type=float, default=0.5, help="share of each domain in train"
    )
    C.add_tasks_arg(split)
    split.set_defaults(func=run_split)

    imp = sub.add_parser(
        "import",
        help="read an existing Claude Code project into a harness tree",
        formatter_class=C.HelpFormatter,
    )
    imp.add_argument(
        "--from",
        dest="source",
        type=Path,
        required=True,
        metavar="PROJECT_DIR",
        help="Claude Code project directory to read",
    )
    imp.add_argument(
        "--dest",
        type=Path,
        required=True,
        metavar="HARNESS_DIR",
        help="harness directory to create",
    )
    imp.set_defaults(func=run_import)
    return p


def run(args: argparse.Namespace) -> int:
    """Dispatch to the chosen action (argparse sets `func` per action)."""
    return args.func(args)


def run_new(args: argparse.Namespace) -> int:
    from .. import tasks as T

    d = T.scaffold(C.path(args.tasks), args.domain, args.name, instruction=args.instruction)
    print(f"created {d}")
    print(f"  instruction.md       the task prompt{'' if args.instruction else ' (edit it)'}")
    print("  task.json            deliverables, timeout_s, max_budget_usd, effort")
    print("  workspace/           files copied into the agent's working directory")
    print("  rubric.json.example  rename to rubric.json (or add check.py) to enable judging")
    print(f"task id: {args.domain}/{args.name}")
    return 0


def run_list(args: argparse.Namespace) -> int:
    from .. import tasks as T

    root = C.path(args.tasks)
    found = T.discover(root)
    rows, bad = [], 0
    for task_id, domain in found.items():
        try:
            t = T.load(root, task_id)
        except (OSError, ValueError) as exc:
            bad += 1
            rows.append((task_id, domain, "-", f"INVALID: {exc}"))
            continue
        judge = (
            "check.py"
            if (t.dir / "check.py").is_file()
            else "rubric.json"
            if (t.dir / "rubric.json").is_file()
            else "-"
        )
        rows.append((task_id, domain, judge, ", ".join(t.deliverables)))
    head = ("task", "domain", "judge", "deliverables")
    widths = [max(len(h), *(len(r[i]) for r in rows)) for i, h in enumerate(head[:3])]
    print("  ".join(h.ljust(w) for h, w in zip(head, widths + [0])).rstrip())
    for r in rows:
        print("  ".join(c.ljust(w) for c, w in zip(r, widths + [0])).rstrip())
    print(f"# {len(found)} task(s) under {root}")
    return 1 if bad else 0


def run_split(args: argparse.Namespace) -> int:
    from .. import tasks as T

    found = T.discover(C.path(args.tasks))
    out = T.write_split(found, args.seed, C.path(args.output), args.train_frac)
    print(f"wrote {out}; use it with: harness2 run --split train --split-file {out}")
    return 0


def run_import(args: argparse.Namespace) -> int:
    from ..core import tree as HT
    from ..substrate.surface import CLAUDE_CODE_SURFACE, unrender

    src, dest = C.path(args.source), C.path(args.dest)
    if not src.is_dir():
        raise FileNotFoundError(f"--from {src} is not a directory")
    if dest.exists() and (not dest.is_dir() or any(dest.iterdir())):
        raise FileExistsError(f"--dest {dest} exists and is not empty; choose a new directory")
    tree = unrender(src)
    if not tree:
        raise ValueError(
            f"{src} has no CLAUDE.md, .claude/agents, .claude/skills, scripts/*.py or hooks to "
            f"import"
        )
    base = HT.read_tree(CLAUDE_CODE_SURFACE, config.base_harness_dir())
    for slot in CLAUDE_CODE_SURFACE.always_on:
        if slot not in tree and slot in base:
            tree[slot] = base[slot]
    repaired = HT.autotrigger(CLAUDE_CODE_SURFACE, tree)
    HT.write_tree(CLAUDE_CODE_SURFACE, tree, dest)
    for rel in sorted(tree):
        print(f"  {rel}")
    if repaired:
        print(f"added trigger lines to guardrails.md for skill(s): {', '.join(repaired)}")
    try:
        census = HT.validate(CLAUDE_CODE_SURFACE, tree)
    except ValueError as exc:
        print(f"wrote {dest}, but it does not validate yet; fix these before a run:")
        for problem in str(exc).split(" | "):
            print(f"  - {problem}")
        return 1
    print(f"wrote {dest} ({', '.join(f'{k}={v}' for k, v in census.items())})")
    print(f"start a run from it: harness2 run --base-harness {dest} --k 1 --dry-run")
    return 0
