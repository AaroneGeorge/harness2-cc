"""Install a harness tree into a workspace exactly as an executor run sees it: the always-on
files become CLAUDE.md, skills and agents go under .claude/, helpers under scripts/, and
hooks.json is merged into .claude/settings.json. Re-rendering first undoes the previous render.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from . import _common as C


def add_parser(subparsers) -> argparse.ArgumentParser:
    p = subparsers.add_parser(
        "render",
        help="install a harness tree into a workspace directory",
        description=__doc__,
        formatter_class=C.HelpFormatter,
    )
    p.add_argument("harness_dir", type=Path, metavar="HARNESS_DIR")
    p.add_argument("workspace_dir", type=Path, metavar="WORKSPACE_DIR")
    p.set_defaults(func=run)
    return p


def run(args: argparse.Namespace) -> int:
    from ..core import tree as HT
    from ..substrate.surface import CLAUDE_CODE_SURFACE, MANIFEST, render

    harness, ws = C.path(args.harness_dir), C.path(args.workspace_dir)
    if not HT.is_tree(CLAUDE_CODE_SURFACE, harness):
        raise FileNotFoundError(f"{harness} is not a harness tree (no always-on file)")
    render(harness, ws)
    manifest = ws / MANIFEST
    written = []
    if manifest.is_file():
        written = sorted(json.loads(manifest.read_text(encoding="utf-8")).get("files") or {})
    for rel in written:
        print(f"  {ws / rel}")
    print(f"rendered {harness} into {ws} ({len(written)} file(s))")
    return 0
