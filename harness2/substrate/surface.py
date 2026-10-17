"""The Claude Code editable surface (c1..c8) and its install into an executor workspace.

`render()` installs a harness tree into a workspace the way Claude Code discovers it:
the four always-on files become one `<ws>/CLAUDE.md`, skills go to `.claude/skills/`, subagents
to `.claude/agents/`, helpers to `scripts/`, and `hooks.json` is merged into
`.claude/settings.json`. Every file `render()` writes or overwrites is recorded in a manifest
(with the content it replaced), so a later `render()` first restores the workspace exactly and
the result never depends on what was rendered before. `unrender()` is the inverse over an
existing Claude Code project.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

from ..core import tree as HT
from ..core.tree import SurfaceSpec, Tree

CLAUDE_CODE_SURFACE = SurfaceSpec(
    name="claude_code",
    always_on=("CLAUDE.md", "guardrails.md", "MEMORY.md", "tool_guidance.md"),
    always_on_max=24_000,
    always_on_cap_reason=(
        "every token here is in context on every turn and competes with the agent's own "
        "output budget"
    ),
    skill_dir="skills",
    skill_name_rule="overrides_dir",
    trigger_hint=(
        "Fix it by adding a trigger line naming `{name}` to CLAUDE.md or guardrails.md, "
        'e.g. "When <the situation this skill handles>, use the `{name}` skill."'
    ),
    autotrigger_file="guardrails.md",
    trigger_line="- When {desc}, use the `{name}` skill.\n",
    subagent_dir="agents",
    subagent_requires_mode=False,
    plugin_dir=None,
    hooks_file="hooks.json",
    hook_events=frozenset(
        {
            "PreToolUse",
            "PostToolUse",
            "UserPromptSubmit",
            "Stop",
            "SubagentStart",
            "SubagentStop",
            "SessionStart",
            "SessionEnd",
            "PreCompact",
            "Notification",
            "PermissionRequest",
        }
    ),
    compile_skill_scripts=True,
    reject_empty_always_on=False,
    stubs=(
        (
            "scripts/README.md",
            "<!-- c7: deterministic helpers, run as `python3 scripts/<name>.py`. "
            "Add one by emitting scripts/<name>.py. -->\n",
        ),
        (
            "agents/README.md",
            "<!-- c5: subagents, invoked with the Agent tool in their OWN context. Add one as "
            "agents/<name>.md with frontmatter `name: <name>` and `description:`. -->\n",
        ),
        (
            "skills/README.md",
            "<!-- c6: reusable workflows loaded on demand. Add one as skills/<name>/SKILL.md "
            "with a `description:` (and optional `name:` matching the directory). -->\n",
        ),
    ),
)

# Section headers render() writes between the always-on slots, in slot order after CLAUDE.md.
SECTION_HEADERS = (
    ("guardrails.md", "## Guardrails"),
    ("MEMORY.md", "## Memory"),
    ("tool_guidance.md", "## Tool guidance"),
)

MANIFEST = ".claude/.harness2-render.json"
SETTINGS = ".claude/settings.json"

_COMMENT_ONLY = re.compile(r"\s*(?:<!--.*?-->\s*)*", re.S)


def _is_empty(text: str) -> bool:
    """Blank, or nothing but HTML comments (the v0 slot descriptions)."""
    return _COMMENT_ONLY.fullmatch(text or "") is not None


def compose_always_on(tree: Tree) -> str:
    """The workspace CLAUDE.md body for a tree, or "" when every always-on slot is empty."""
    parts: list[str] = []
    c1 = tree.get("CLAUDE.md", "")
    if not _is_empty(c1):
        parts.append(c1.strip())
    for rel, header in SECTION_HEADERS:
        body = tree.get(rel, "")
        if not _is_empty(body):
            parts.append(f"{header}\n\n{body.strip()}")
    return "\n\n".join(parts) + "\n" if parts else ""


def _install_path(rel: str) -> str | None:
    """Workspace path for one non-always-on tree file, or None when it is not installed."""
    if rel.startswith("skills/"):
        return ".claude/" + rel
    if rel.startswith("agents/"):
        return ".claude/" + rel
    if rel.startswith("scripts/"):
        return rel
    return None


def _with_agent_name(rel: str, text: str) -> str:
    """Claude Code keys a subagent by its frontmatter `name:`; add one equal to the file stem
    when fenced frontmatter lacks it (validation already rejects a mismatched one)."""
    if not text.startswith("---\n"):
        return text
    end = text.find("\n---", 4)
    fm = text[4:end] if end != -1 else ""
    if end == -1 or re.search(r"^name:", fm, re.M):
        return text
    return f"---\nname: {Path(rel).stem}\n" + text[4:]


def _restore(ws: Path) -> None:
    """Undo a previous render(): put back what it replaced, remove what it created."""
    mp = ws / MANIFEST
    try:
        manifest = json.loads(mp.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return
    dirs: set[Path] = set()
    for rel, original in (manifest.get("files") or {}).items():
        try:
            p = ws / HT.normalize_rel(rel)
        except ValueError:
            continue
        if original is None:
            if p.is_file():
                p.unlink()
            dirs.update(q for q in p.parents if q != ws and ws in q.parents)
        else:
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(original, encoding="utf-8")
    for d in sorted(dirs, key=lambda q: len(q.parts), reverse=True):
        try:
            d.rmdir()
        except OSError:
            pass
    mp.unlink()


def _merge_hooks(existing: dict, extra: dict) -> dict:
    out = {k: list(v) if isinstance(v, list) else v for k, v in existing.items()}
    for event, entries in extra.items():
        cur = out.get(event)
        out[event] = (cur if isinstance(cur, list) else []) + list(entries)
    return out


def render(harness_dir: Path, workspace: Path) -> None:
    """Install the harness tree at `harness_dir` into `workspace` (see DESIGN §3)."""
    ws = Path(workspace)
    ws.mkdir(parents=True, exist_ok=True)
    _restore(ws)
    tree = HT.read_tree(CLAUDE_CODE_SURFACE, harness_dir)

    writes: dict[str, str] = {}
    body = compose_always_on(tree)
    if body:
        # A CLAUDE.md the task workspace already ships is kept, after the harness sections.
        prior = ws / "CLAUDE.md"
        if prior.is_file():
            body += "\n" + prior.read_text(encoding="utf-8", errors="replace")
        writes["CLAUDE.md"] = body
    for rel, text in sorted(tree.items()):
        dest = _install_path(rel)
        if dest is None:
            continue
        if rel.startswith("agents/"):
            text = _with_agent_name(rel, text)
        writes[dest] = text
    hooks_src = tree.get(CLAUDE_CODE_SURFACE.hooks_file or "", "")
    if hooks_src.strip():
        try:
            hooks = json.loads(hooks_src)["hooks"]
            if not isinstance(hooks, dict):
                raise TypeError("`hooks` is not an object")
        except (ValueError, KeyError, TypeError) as exc:
            raise ValueError(f"{harness_dir}/hooks.json cannot be installed: {exc}") from exc
        sp = ws / SETTINGS
        settings = {}
        if sp.is_file():
            settings = json.loads(sp.read_text(encoding="utf-8"))
            if not isinstance(settings, dict):
                raise ValueError(f"{sp} is not a JSON object; cannot merge hooks into it")
        cur = settings.get("hooks") if isinstance(settings.get("hooks"), dict) else {}
        settings["hooks"] = _merge_hooks(cur, hooks)
        writes[SETTINGS] = json.dumps(settings, indent=2) + "\n"

    originals: dict[str, str | None] = {}
    for rel, text in writes.items():
        p = ws / rel
        originals[rel] = p.read_text(encoding="utf-8", errors="replace") if p.is_file() else None
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text, encoding="utf-8")
    if originals:
        mp = ws / MANIFEST
        mp.parent.mkdir(parents=True, exist_ok=True)
        mp.write_text(json.dumps({"files": originals}, indent=2, sort_keys=True), encoding="utf-8")


def _split_claude_md(text: str) -> Tree:
    """Split a CLAUDE.md written by render() back into its slots; any other file is c1 whole."""
    lines = text.splitlines(keepends=True)
    cuts: list[tuple[int, str]] = []
    end = len(lines)
    for rel, header in reversed(SECTION_HEADERS):
        for i in range(end - 1, -1, -1):
            if lines[i].rstrip("\r\n") == header:
                cuts.append((i, rel))
                end = i
                break
    out: Tree = {}
    if not cuts:
        if text.strip():
            out["CLAUDE.md"] = text
        return out
    cuts.sort()
    head = "".join(lines[: cuts[0][0]]).strip()
    if head:
        out["CLAUDE.md"] = head + "\n"
    for n, (i, rel) in enumerate(cuts):
        stop = cuts[n + 1][0] if n + 1 < len(cuts) else len(lines)
        body = "".join(lines[i + 1 : stop]).strip()
        if body:
            out[rel] = body + "\n"
    return out


def unrender(project_dir: Path) -> Tree:
    """Read an existing Claude Code project back into a harness tree (inverse of render)."""
    root = Path(project_dir)
    surface = CLAUDE_CODE_SURFACE
    out: Tree = {}
    cm = root / "CLAUDE.md"
    if cm.is_file():
        out.update(_split_claude_md(cm.read_text(encoding="utf-8", errors="replace")))
    sources = (
        (root / ".claude" / "skills", "skills"),
        (root / ".claude" / "agents", "agents"),
        (root / "scripts", "scripts"),
    )
    for base, prefix in sources:
        if not base.is_dir():
            continue
        for p in sorted(base.rglob("*")):
            if not p.is_file() or "__pycache__" in p.parts:
                continue
            rel = f"{prefix}/{p.relative_to(base).as_posix()}"
            if HT.is_allowed(surface, rel) and rel not in HT._stub_paths(surface):
                out[rel] = p.read_text(encoding="utf-8", errors="replace")
    sp = root / SETTINGS
    if sp.is_file():
        try:
            settings = json.loads(sp.read_text(encoding="utf-8"))
        except ValueError:
            settings = None
        hooks = settings.get("hooks") if isinstance(settings, dict) else None
        if isinstance(hooks, dict) and hooks:
            out[surface.hooks_file or "hooks.json"] = json.dumps({"hooks": hooks}, indent=2) + "\n"
    return out
