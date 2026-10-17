"""render()/unrender(): installing a harness tree into a Claude Code workspace and back."""

from __future__ import annotations

import json

from harness2 import config
from harness2.core import tree as HT
from harness2.substrate import CLAUDE_CODE_SURFACE as S
from harness2.substrate import render, unrender
from harness2.substrate.surface import MANIFEST, compose_always_on

HOOKS = {
    "hooks": {
        "PreToolUse": [
            {
                "matcher": "Bash",
                "hooks": [{"type": "command", "command": "python3 scripts/guard.py"}],
            }
        ]
    }
}


def full_tree() -> dict[str, str]:
    return {
        "CLAUDE.md": "You are a careful analyst.\n",
        "guardrails.md": "- When reconciling, use the `recon` skill.\n",
        "MEMORY.md": "Ledgers are signed.\n",
        "tool_guidance.md": "Prefer Grep over Bash grep.\n",
        "skills/recon/SKILL.md": "---\ndescription: reconciling\n---\nSteps.\n",
        "skills/recon/scripts/sum.py": "print(1)\n",
        "agents/checker.md": "---\nname: checker\ndescription: checks\n---\nCheck.\n",
        "scripts/guard.py": "import sys\n",
        "hooks.json": json.dumps(HOOKS, indent=2) + "\n",
    }


def _harness(tmp_path, tree, name="h"):
    return HT.write_tree(S, tree, tmp_path / name)


def test_compose_always_on_sections():
    body = compose_always_on(full_tree())
    assert body == (
        "You are a careful analyst.\n\n## Guardrails\n\n- When reconciling, use the `recon` "
        "skill.\n\n## Memory\n\nLedgers are signed.\n\n## Tool guidance\n\nPrefer Grep over "
        "Bash grep.\n"
    )
    assert compose_always_on({"MEMORY.md": "<!-- only a comment -->\n", "CLAUDE.md": " \n"}) == ""
    assert compose_always_on({"MEMORY.md": "fact"}) == "## Memory\n\nfact\n"


def test_render_layout(tmp_path):
    ws = tmp_path / "ws"
    render(_harness(tmp_path, full_tree()), ws)
    assert (ws / "CLAUDE.md").read_text().startswith("You are a careful analyst.\n\n## Guardrails")
    assert (ws / ".claude/skills/recon/SKILL.md").is_file()
    assert (ws / ".claude/skills/recon/scripts/sum.py").read_text() == "print(1)\n"
    assert (ws / ".claude/agents/checker.md").is_file()
    assert (ws / "scripts/guard.py").is_file()
    assert not (ws / "scripts/README.md").exists()  # stubs are not installed
    settings = json.loads((ws / ".claude/settings.json").read_text())
    assert settings == {"hooks": HOOKS["hooks"]}


def test_empty_v0_writes_no_claude_md(tmp_path):
    ws = tmp_path / "ws"
    render(config.base_harness_dir(), ws)
    assert not (ws / "CLAUDE.md").exists()
    assert not (ws / ".claude/settings.json").exists()
    assert not (ws / "scripts").exists()
    back = unrender(ws)
    assert back == {}
    HT.validate(S, back)


def test_render_unrender_round_trip(tmp_path):
    tree = full_tree()
    ws = tmp_path / "ws"
    render(_harness(tmp_path, tree), ws)
    back = unrender(ws)
    assert back.keys() == tree.keys()
    for rel in tree:
        if rel != "hooks.json":
            assert back[rel] == tree[rel], rel
    assert json.loads(back["hooks.json"]) == HOOKS
    HT.validate(S, back)


def test_rerender_is_exact_and_keeps_foreign_files(tmp_path):
    ws = tmp_path / "ws"
    (ws / "scripts").mkdir(parents=True)
    (ws / "data.csv").write_text("a,b\n")
    (ws / "scripts/task_tool.py").write_text("# from the task\n")
    (ws / ".claude").mkdir()
    (ws / ".claude/settings.json").write_text(
        json.dumps(
            {
                "model": "x",
                "hooks": {"Stop": [{"hooks": [{"type": "command", "command": "echo task"}]}]},
            }
        )
    )

    render(_harness(tmp_path, full_tree(), "a"), ws)
    merged = json.loads((ws / ".claude/settings.json").read_text())
    assert merged["model"] == "x"
    assert set(merged["hooks"]) == {"Stop", "PreToolUse"}

    small = {"CLAUDE.md": "Only this.\n", "scripts/other.py": "x = 1\n"}
    render(_harness(tmp_path, small, "b"), ws)
    assert (ws / "CLAUDE.md").read_text() == "Only this.\n"
    assert not (ws / ".claude/skills/recon").exists()
    assert not (ws / ".claude/agents/checker.md").exists()
    assert not (ws / "scripts/guard.py").exists()
    assert (ws / "scripts/other.py").is_file()
    assert (ws / "scripts/task_tool.py").read_text() == "# from the task\n"
    assert (ws / "data.csv").read_text() == "a,b\n"
    restored = json.loads((ws / ".claude/settings.json").read_text())
    assert restored == {
        "model": "x",
        "hooks": {"Stop": [{"hooks": [{"type": "command", "command": "echo task"}]}]},
    }

    render(config.base_harness_dir(), ws)
    assert not (ws / "CLAUDE.md").exists()
    assert not (ws / "scripts/other.py").exists()
    assert not (ws / MANIFEST).exists()


def test_existing_workspace_claude_md_is_kept(tmp_path):
    ws = tmp_path / "ws"
    ws.mkdir()
    (ws / "CLAUDE.md").write_text("Task notes.\n")
    render(_harness(tmp_path, {"CLAUDE.md": "Harness.\n"}), ws)
    text = (ws / "CLAUDE.md").read_text()
    assert text.startswith("Harness.\n") and text.endswith("Task notes.\n")
    render(config.base_harness_dir(), ws)
    assert (ws / "CLAUDE.md").read_text() == "Task notes.\n"


def test_agent_without_name_gets_one(tmp_path):
    tree = {"CLAUDE.md": "x\n", "agents/helper.md": "---\ndescription: helps\n---\nBody.\n"}
    ws = tmp_path / "ws"
    render(_harness(tmp_path, tree), ws)
    text = (ws / ".claude/agents/helper.md").read_text()
    assert text == "---\nname: helper\ndescription: helps\n---\nBody.\n"


def test_unrender_plain_project(tmp_path):
    proj = tmp_path / "proj"
    (proj / ".claude/skills/demo").mkdir(parents=True)
    (proj / ".claude/skills/demo/SKILL.md").write_text("---\ndescription: d\n---\n")
    (proj / ".claude/skills/demo/notes.txt").write_text("not on the surface")
    (proj / "CLAUDE.md").write_text("# Project\n\n## Setup\n\nRun it.\n")
    (proj / "src").mkdir()
    (proj / "src/app.py").write_text("x = 1\n")
    back = unrender(proj)
    assert back == {
        "CLAUDE.md": "# Project\n\n## Setup\n\nRun it.\n",
        "skills/demo/SKILL.md": "---\ndescription: d\n---\n",
    }


def test_unrender_splits_only_render_headers(tmp_path):
    proj = tmp_path / "p"
    proj.mkdir()
    (proj / "CLAUDE.md").write_text("Intro.\n\n## Memory\n\nA fact.\n")
    assert unrender(proj) == {"CLAUDE.md": "Intro.\n", "MEMORY.md": "A fact.\n"}
