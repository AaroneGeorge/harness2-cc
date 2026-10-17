"""Tree rules on the Claude Code surface: validation, autotrigger, round trip, diff, hooks."""

from __future__ import annotations

import json

import pytest

from harness2 import config
from harness2.core import tree as HT
from harness2.substrate.surface import CLAUDE_CODE_SURFACE as S

SKILL = "---\ndescription: reconciling two ledgers\n---\nSteps.\n"
AGENT = "---\nname: checker\ndescription: re-checks totals\n---\nYou check totals.\n"
HOOKS = {
    "hooks": {
        "PreToolUse": [
            {"matcher": "Bash", "hooks": [{"type": "command", "command": "python3 scripts/g.py"}]}
        ],
        "Stop": [{"hooks": [{"type": "command", "command": "true", "timeout": 5}]}],
    }
}


def good_tree() -> dict[str, str]:
    return {
        "CLAUDE.md": "You are careful.\n",
        "guardrails.md": "- When reconciling, use the `recon` skill.\n",
        "MEMORY.md": "",
        "tool_guidance.md": "",
        "skills/recon/SKILL.md": SKILL,
        "skills/recon/scripts/sum.py": "print(1)\n",
        "agents/checker.md": AGENT,
        "scripts/g.py": "import sys\n",
        "hooks.json": json.dumps(HOOKS),
    }


def errors(tree, **kw) -> str:
    with pytest.raises(ValueError) as ei:
        HT.validate(S, tree, **kw)
    return str(ei.value)


def test_surface_paths():
    assert S.subagent_dir == "agents"
    for rel in (
        "CLAUDE.md",
        "MEMORY.md",
        "skills/a/SKILL.md",
        "skills/a/scripts/x.sh",
        "agents/a.md",
        "scripts/a_b.py",
        "hooks.json",
    ):
        assert HT.is_allowed(S, rel), rel
    for rel in (
        "sub_agents/a/AGENT.md",
        "agents/a/AGENT.md",
        "plugin/a.ts",
        "scripts/A.py",
        "../CLAUDE.md",
        "/CLAUDE.md",
        "notes.md",
        "skills/a/other.md",
    ):
        assert not HT.is_allowed(S, rel), rel
    assert HT.subagent_names(S, good_tree()) == ["checker"]
    assert HT.skill_names(S, good_tree()) == ["recon"]
    assert HT.subagent_path(S, "x") == "agents/x.md"


def test_upstream_subagent_layout_still_default():
    spec = HT.SurfaceSpec(
        name="up",
        always_on=("a.md",),
        always_on_max=100,
        always_on_cap_reason="r",
        skill_dir="skills",
        skill_name_rule="must_match",
        trigger_hint="{name}",
        autotrigger_file="a.md",
        trigger_line="{name} {desc}\n",
        subagent_requires_mode=True,
        plugin_dir=None,
        hooks_file=None,
    )
    assert HT.is_allowed(spec, "sub_agents/x/AGENT.md")
    assert not HT.is_allowed(spec, "agents/x.md")
    with pytest.raises(ValueError, match="needs frontmatter `mode: subagent`"):
        HT.validate(spec, {"a.md": "", "sub_agents/x/AGENT.md": "description: d\n"})


def test_good_tree_validates():
    census = HT.validate(S, good_tree())
    assert census["skills"] == 1 and census["sub_agents"] == 1
    assert census["scripts"] == 1 and census["hooks"] == 1
    assert "plugins" not in census


def test_empty_v0_tree_validates():
    tree = HT.read_tree(S, config.base_harness_dir())
    assert set(tree) == set(S.always_on)
    HT.validate(S, tree)


def test_always_on_cap():
    t = good_tree()
    t["MEMORY.md"] = "x" * (S.always_on_max + 1)
    assert "over the 24000 cap" in errors(t)
    assert "over the 50 cap" in errors(good_tree(), always_on_max=50)


def test_skill_needs_description():
    t = good_tree()
    t["skills/recon/SKILL.md"] = "---\nname: recon\n---\n"
    assert "needs a `description:`" in errors(t)


def test_skill_name_must_equal_dir():
    t = good_tree()
    t["skills/recon/SKILL.md"] = "---\nname: other\ndescription: d\n---\n"
    assert "OVERRIDES the directory name" in errors(t)
    t["skills/recon/SKILL.md"] = "---\nname: recon\ndescription: d\n---\n"
    HT.validate(S, t)


def test_untriggered_skill():
    t = good_tree()
    t["guardrails.md"] = ""
    msg = errors(t)
    assert "never named in the always-on text" in msg and "use the `recon` skill" in msg


def test_subagent_needs_description():
    t = good_tree()
    t["agents/checker.md"] = "---\nname: checker\n---\n"
    assert "agents/checker.md needs a `description:`" in errors(t)


def test_subagent_name_must_equal_stem():
    t = good_tree()
    t["agents/checker.md"] = "---\nname: auditor\ndescription: d\n---\n"
    assert "must equal the file name" in errors(t)
    t["agents/checker.md"] = "---\ndescription: d\n---\n"  # name optional
    HT.validate(S, t)


def test_subagents_disallowed():
    assert "not available on this deployment" in errors(good_tree(), allow_subagents=False)


def test_script_must_compile():
    t = good_tree()
    t["scripts/g.py"] = "def (:\n"
    assert "scripts/g.py does not compile" in errors(t)


def test_skill_script_must_compile():
    t = good_tree()
    t["skills/recon/scripts/sum.py"] = "x = = 1\n"
    assert "skills/recon/scripts/sum.py does not compile" in errors(t)
    t["skills/recon/scripts/sum.py"] = "not python at all ((\n"
    t["skills/recon/scripts/run.sh"] = "echo ((\n"  # non-.py skill scripts are not compiled
    del t["skills/recon/scripts/sum.py"]
    HT.validate(S, t)


def test_script_reference_must_exist():
    t = good_tree()
    t["CLAUDE.md"] += "Run `python3 scripts/missing.py` first.\n"
    assert "invokes `scripts/missing.py`" in errors(t)
    t = good_tree()
    t["skills/recon/SKILL.md"] += "Run python3 skills/recon/scripts/nope.py\n"
    assert "invokes `skills/recon/scripts/nope.py`" in errors(t)
    t = good_tree()
    t["CLAUDE.md"] += "Run python3 scripts/g.py and python skills/recon/scripts/sum.py\n"
    HT.validate(S, t)
    assert HT.RESERVED_SCRIPTS == frozenset()


def test_script_reference_runtime_path_resolves_to_tree_path():
    # At runtime a skill helper lives at `.claude/skills/<s>/scripts/<f>.py` in the workspace;
    # the SKILL.md may invoke it by that path and it must resolve to `skills/<s>/scripts/<f>.py`.
    t = good_tree()
    t["skills/recon/SKILL.md"] += "Run `python3 .claude/skills/recon/scripts/sum.py ledger.csv`.\n"
    t["CLAUDE.md"] += "Or python .claude/skills/recon/scripts/sum.py; also python3 scripts/g.py\n"
    HT.validate(S, t)


def test_script_reference_runtime_path_missing_is_reported():
    t = good_tree()
    t["skills/recon/SKILL.md"] += "Run python3 .claude/skills/recon/scripts/nope.py\n"
    assert "invokes `.claude/skills/recon/scripts/nope.py`" in errors(t)
    # a `.claude/` prefix only applies to skill helpers; loose helpers stay at scripts/<name>.py
    t = good_tree()
    t["CLAUDE.md"] += "Run python3 scripts/missing.py\n"
    assert "invokes `scripts/missing.py`" in errors(t)


def test_errors_are_all_reported():
    t = good_tree()
    t["scripts/g.py"] = "def (:\n"
    t["agents/checker.md"] = "nothing\n"
    msg = errors(t)
    assert " | " in msg and "does not compile" in msg and "description" in msg


@pytest.mark.parametrize(
    "hooks, needle",
    [
        ("{not json", "not valid JSON"),
        ("[]", "needs a top-level"),
        ('{"hooks": {}}', "needs a top-level"),
        ('{"hooks": {"BeforeTool": []}}', "unknown event 'BeforeTool'"),
        ('{"hooks": {"Stop": {}}}', "must map to a LIST"),
        ('{"hooks": {"Stop": [{"matcher": "x"}]}}', "needs a `hooks` list"),
        (
            '{"hooks": {"Stop": [{"hooks": [{"type": "prompt", "prompt": "x"}]}]}}',
            'hook type must be "command"',
        ),
        (
            '{"hooks": {"Stop": [{"hooks": [{"type": "command", "command": " "}]}]}}',
            "empty `command`",
        ),
        (
            '{"hooks": {"Stop": [{"hooks": [{"type": "command", "command": "x", "timeout": "5"}]}]}}',
            "`timeout` must be a positive integer",
        ),
        (
            '{"hooks": {"PreToolUse": [{"matcher": 3, '
            '"hooks": [{"type": "command", "command": "x"}]}]}}',
            "`matcher` must be a string",
        ),
        (
            '{"hooks": {"Stop": [{"hooks": [{"type": "command", "command": "x"}]}]}, "env": {}}',
            "only the top-level `hooks` key",
        ),
    ],
)
def test_hooks_schema_rejects(hooks, needle):
    t = good_tree()
    t["hooks.json"] = hooks
    assert needle in errors(t)


def test_hooks_unknown_event_lists_known_events():
    errs: list[str] = []
    HT.check_hooks(S, '{"hooks": {"preToolUse": []}}', errs)
    assert len(errs) == 1 and "PreToolUse" in errs[0] and "PermissionRequest" in errs[0]


def test_hooks_all_events_accepted():
    doc = {
        "hooks": {e: [{"hooks": [{"type": "command", "command": "true"}]}] for e in S.hook_events}
    }
    errs: list[str] = []
    HT.check_hooks(S, json.dumps(doc), errs)
    assert errs == []


def test_autotrigger_repairs_untriggered_skill():
    t = good_tree()
    t["guardrails.md"] = "Be precise.\n"
    t["skills/other/SKILL.md"] = "---\ndescription: drafting a memo.\n---\n"
    fixed = HT.autotrigger(S, t)
    assert sorted(fixed) == ["other", "recon"]
    assert t["guardrails.md"].startswith("Be precise.\n")
    assert "- When drafting a memo, use the `other` skill.\n" in t["guardrails.md"]
    assert "- When reconciling two ledgers, use the `recon` skill.\n" in t["guardrails.md"]
    HT.validate(S, t)
    assert HT.autotrigger(S, t) == []


def test_autotrigger_skips_over_cap():
    t = good_tree()
    t["guardrails.md"] = ""
    assert HT.autotrigger(S, t, always_on_max=20) == []
    assert t["guardrails.md"] == ""


def test_read_write_round_trip(tmp_path):
    t = good_tree()
    dest = HT.write_tree(S, t, tmp_path / "h")
    for rel, _ in S.stubs:
        assert (dest / rel).is_file()
    (dest / "notes.txt").write_text("keep me")
    rejected: list[str] = []
    back = HT.read_tree(S, dest, rejected=rejected)
    assert back == t
    assert rejected == ["notes.txt"]
    assert HT.is_tree(S, dest)
    # Rewriting an exact smaller tree removes editable files but keeps foreign ones.
    small = {"CLAUDE.md": "hi\n"}
    HT.write_tree(S, small, dest)
    back = HT.read_tree(S, dest)
    assert back == {
        "CLAUDE.md": "hi\n",
        "guardrails.md": "",
        "MEMORY.md": "",
        "tool_guidance.md": "",
    }
    assert (dest / "notes.txt").read_text() == "keep me"
    assert not (dest / "agents" / "checker.md").exists()


def test_normalize_rel():
    assert HT.normalize_rel("./skills/a/SKILL.md") == "skills/a/SKILL.md"
    for bad in ("../x", "/abs", "a/../../b"):
        with pytest.raises(ValueError):
            HT.normalize_rel(bad)


def test_diff_summary():
    a = good_tree()
    b = dict(a)
    b["MEMORY.md"] = "fact\n"
    del b["scripts/g.py"]
    b["scripts/new.py"] = "x = 1\n"
    d = HT.diff_summary(S, a, b)
    assert d["added"] == ["scripts/new.py"]
    assert d["removed"] == ["scripts/g.py"]
    assert d["modified"] == ["MEMORY.md"]
    assert d["always_on_char_delta"] == len("fact\n")


def test_diversity():
    a = good_tree()
    assert HT.diversity({"a": a}) == {"n": 1, "n_distinct": 1, "mean_line_jaccard": 1.0}
    same = HT.diversity({"a": a, "b": dict(a)})
    assert same["n_distinct"] == 1 and same["mean_line_jaccard"] == 1.0
    other = {"CLAUDE.md": "completely different\n"}
    diff = HT.diversity({"a": a, "b": other})
    assert diff["n_distinct"] == 2 and diff["mean_line_jaccard"] == 0.0
