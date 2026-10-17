"""Install the harness a finished run adopted for one task into a Claude Code project.

The adopted harness is `<runs>/adapt/<mode ns>/<task>/harnesses/<chosen label>/`, read from
the task's summary.json. Into --dest it writes: CLAUDE.md (created, or the harness sections
appended under a `<!-- harness2 -->` marker line when the project already has one),
.claude/agents/, .claude/skills/, scripts/, and hooks.json merged into .claude/settings.json
(existing settings kept). It refuses to overwrite any existing file unless --force, and prints
every path it writes.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from . import _common as C

MARKER = "<!-- harness2 -->"


def add_parser(subparsers) -> argparse.ArgumentParser:
    p = subparsers.add_parser(
        "export",
        help="install a task's adopted harness into a Claude Code project",
        description=__doc__,
        formatter_class=C.HelpFormatter,
    )
    C.add_runs_arg(p)
    p.add_argument(
        "--task", required=True, metavar="ID", help="task id, e.g. data-cleanup/ledger-reconcile"
    )
    C.add_k_arg(p)
    p.add_argument(
        "--mode",
        default="parallel",
        metavar="MODE",
        help="parallel | sequential: whose adopted harness",
    )
    C.add_seed_arg(p)
    C.add_tag_arg(p)
    p.add_argument(
        "--dest",
        type=Path,
        required=True,
        metavar="PROJECT_DIR",
        help="project directory to install into",
    )
    p.add_argument("--force", action="store_true", help="overwrite existing files")
    p.set_defaults(func=run)
    return p


def locate(
    runs: Path, task_id: str, k: int, mode: str, seed: int | None, tag: str = ""
) -> tuple[Path, dict]:
    """(adopted harness dir, selection record) for one task and mode of a finished run.

    Looks under `<runs>/adapt/` (a `harness2 run`) and directly under `<runs>/` (the layout
    `harness2 demo` writes under <output>/runs).
    """
    slug = C.task_slug(task_id)
    tried = []
    for base in (C.runs_root_for(runs), runs):
        for bench in (C.BENCH, "demo"):
            summary_path = base / C.summary_namespace(k, seed=seed, tag=tag, bench=bench) / slug
            summary_path = summary_path / "summary.json"
            tried.append(summary_path)
            if not summary_path.is_file():
                continue
            summary = json.loads(summary_path.read_text(encoding="utf-8"))
            sel = (summary.get("selections") or {}).get(mode) or {}
            label = sel.get("chosen_label")
            if not sel.get("ok") or not label:
                raise RuntimeError(
                    f"{summary_path}: the {C.mode_label(mode)} mode has no adopted harness "
                    f"(selection did not complete); re-run the task"
                )
            harness = base / C.namespace(mode, k, seed=seed, tag=tag, bench=bench) / slug
            harness = harness / "harnesses" / label
            if not harness.is_dir():
                raise FileNotFoundError(f"adopted harness {harness} is missing")
            return harness, sel
    raise FileNotFoundError(
        f"no summary for task {task_id!r} at k={k}"
        + (f", seed={seed}" if seed is not None else "")
        + (f", tag={tag!r}" if tag else "")
        + f"; looked at {tried[0]} (is --runs right, and did the task finish?)"
    )


def _merge_hooks(existing: dict, extra: dict) -> dict:
    out = {k: list(v) if isinstance(v, list) else v for k, v in existing.items()}
    for event, entries in extra.items():
        cur = out.get(event) if isinstance(out.get(event), list) else []
        out[event] = cur + [e for e in entries if e not in cur]
    return out


def plan(tree: dict[str, str], dest: Path, *, force: bool) -> tuple[dict[str, str], list[str]]:
    """(dest-relative path -> full new content, conflicts) for installing `tree` into `dest`."""
    from ..substrate import surface as S

    writes: dict[str, str] = {}
    conflicts: list[str] = []
    body = S.compose_always_on(tree)
    if body:
        cm = dest / "CLAUDE.md"
        if not cm.is_file():
            writes["CLAUDE.md"] = f"{MARKER}\n{body}"
        else:
            prior = cm.read_text(encoding="utf-8", errors="replace")
            lines = prior.splitlines(keepends=True)
            at = next((i for i, ln in enumerate(lines) if ln.strip() == MARKER), None)
            if at is None:
                sep = "" if prior.endswith("\n") or not prior else "\n"
                writes["CLAUDE.md"] = f"{prior}{sep}\n{MARKER}\n{body}"
            elif force:
                writes["CLAUDE.md"] = "".join(lines[:at]) + f"{MARKER}\n{body}"
            else:
                conflicts.append("CLAUDE.md (already has a harness2 section)")
    for rel, text in sorted(tree.items()):
        target = S._install_path(rel)
        if target is None:
            continue
        if rel.startswith("agents/"):
            text = S._with_agent_name(rel, text)
        if (dest / target).exists() and not force:
            conflicts.append(target)
        writes[target] = text
    hooks_src = tree.get(S.CLAUDE_CODE_SURFACE.hooks_file or "", "")
    if hooks_src.strip():
        hooks = json.loads(hooks_src).get("hooks") or {}
        sp = dest / S.SETTINGS
        settings = {}
        if sp.is_file():
            settings = json.loads(sp.read_text(encoding="utf-8"))
            if not isinstance(settings, dict):
                raise ValueError(f"{sp} is not a JSON object; cannot merge hooks into it")
        cur = settings.get("hooks") if isinstance(settings.get("hooks"), dict) else {}
        merged = _merge_hooks(cur, hooks)
        if merged != cur:
            settings["hooks"] = merged
            writes[S.SETTINGS] = json.dumps(settings, indent=2) + "\n"
    return writes, conflicts


def run(args: argparse.Namespace) -> int:
    from ..core import schedule as SCHED
    from ..core import tree as HT
    from ..substrate.surface import CLAUDE_CODE_SURFACE

    if args.k < 1:
        raise ValueError("--k must be positive")
    mode = SCHED.mode_token(args.mode)
    harness, sel = locate(C.path(args.runs), args.task, args.k, mode, args.seed, args.tag)
    tree = HT.read_tree(CLAUDE_CODE_SURFACE, harness)
    dest = C.path(args.dest)
    dest.mkdir(parents=True, exist_ok=True)
    writes, conflicts = plan(tree, dest, force=args.force)
    if conflicts:
        raise FileExistsError(
            f"refusing to overwrite in {dest}: {', '.join(conflicts)}; pass --force to replace"
        )
    if not writes:
        print(f"the adopted harness ({harness}) is empty: nothing to install")
        return 0
    for rel, text in writes.items():
        p = dest / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text, encoding="utf-8")
        print(f"  wrote {p}")
    print(
        f"installed {C.mode_label(mode)} harness `{sel.get('chosen_label')}` for {args.task} "
        f"from {harness} into {dest}"
    )
    return 0
