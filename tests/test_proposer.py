"""Proposer over a fake `claude -p --output-format json`: reply, harvest, cache, errors."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from fakes import apply_fake_env, calls

from harness2 import config
from harness2.core import proposer as P
from harness2.core.proposer import Proposer, ProposerError, Reply, _parse_json
from harness2.core.spec import ChannelSpec

SPEC = ChannelSpec("claude_cli", "claude-opus-5-5", effort="high", timeout=60)


@pytest.fixture
def log(tmp_path, monkeypatch, fake_claude):
    d = tmp_path / "fakelog"
    apply_fake_env(monkeypatch, log=d)
    return d


def context(tmp_path):
    c = tmp_path / "ctx"
    (c / "harness").mkdir(parents=True, exist_ok=True)
    (c / "harness" / "CLAUDE.md").write_text("v0\n")
    (c / "task.md").write_text("The task.\n")
    return c


def test_call_returns_reply_and_command_shape(tmp_path, log, monkeypatch):
    apply_fake_env(monkeypatch, reply="<notes>edited</notes>")
    monkeypatch.setenv("CLAUDECODE", "1")
    monkeypatch.setenv("CLAUDE_CODE_ENTRYPOINT", "cli")
    prop = Proposer(SPEC, audit_dir=tmp_path / "audit")
    r = prop.call("Do the mission.", context(tmp_path), "inv1.a1", system="Be terse.")
    assert isinstance(r, Reply) and r.ok and bool(r)
    assert r.text == "<notes>edited</notes>"
    assert r.cost_usd == pytest.approx(0.0123)
    assert r.attempts == 1 and not r.cached and r.channel == "claude_cli"
    assert r.audit is not None and r.audit.read_text() == r.text
    assert prop._calls == [r]

    (rec,) = calls(log)
    argv = rec["argv"]
    assert rec["stdin"] == "Do the mission."
    pairs = {
        "--output-format": "json",
        "--model": "claude-opus-5-5",
        "--effort": "high",
        "--permission-mode": "bypassPermissions",
        "--tools": "Read,Edit,Write,Glob,Grep",
        "--setting-sources": "project",
        "--max-budget-usd": str(config.IMPROVER_MAX_BUDGET_USD),
        "--append-system-prompt": "Be terse.",
    }
    for flag, value in pairs.items():
        assert argv[argv.index(flag) + 1] == value
    assert "-p" in argv and "--no-session-persistence" in argv
    assert "--disallowedTools" not in argv
    assert "CLAUDECODE" not in rec["env_keys"]
    assert "CLAUDE_CODE_ENTRYPOINT" not in rec["env_keys"]
    # The session ran in a temp dir holding a copy of the context, which is then removed.
    assert "harness2-prop-" in rec["cwd"] and not Path(rec["cwd"]).exists()

    meta = json.loads(next((tmp_path / "audit").glob("*.meta.json")).read_text())
    assert meta["ok"] and meta["model"] == "claude-opus-5-5" and meta["effort"] == "high"


def test_env_strips_only_nested_session_markers(monkeypatch, fake_claude):
    monkeypatch.setenv("CLAUDECODE", "1")
    monkeypatch.setenv("CLAUDE_CODE_ENTRYPOINT", "cli")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test")
    env = Proposer(SPEC).env()
    assert "CLAUDECODE" not in env and "CLAUDE_CODE_ENTRYPOINT" not in env
    assert env["ANTHROPIC_API_KEY"] == "sk-test"
    assert not any(k.startswith(("CLAUDE_CODE_USE_", "ANTHROPIC_VERTEX")) for k in env)


def test_harvest_returns_edited_harness_and_cache_hit(tmp_path, log, monkeypatch):
    apply_fake_env(monkeypatch, edit="skills/x/SKILL.md=---\ndescription: x\n---\nbody\n")
    prop = Proposer(SPEC, audit_dir=tmp_path / "audit")
    ctx = context(tmp_path)
    r = prop.call("Edit the harness.", ctx, "inv1.a1", harvest="context/harness")
    assert r.workspace is not None and r.workspace.is_dir()
    assert r.workspace.parent == tmp_path / "audit"
    assert (r.workspace / "skills/x/SKILL.md").read_text().endswith("body\n")
    assert (r.workspace / "CLAUDE.md").read_text() == "v0\n"
    assert not (ctx / "harness" / "skills").exists()  # the caller's context is untouched

    again = prop.call("Edit the harness.", ctx, "inv1.a1", harvest="context/harness")
    assert again.cached and again.cost_usd == 0.0 and again.text == r.text
    assert again.workspace == r.workspace
    assert len(calls(log)) == 1  # served from the audit cache
    assert prop._calls == [r, again]

    # A different mission is a different call.
    prop.call("Edit it differently.", ctx, "inv1.a1", harvest="context/harness")
    assert len(calls(log)) == 2


def test_harvest_deletion_edit(tmp_path, log, monkeypatch):
    apply_fake_env(monkeypatch, edit="CLAUDE.md=")
    r = Proposer(SPEC, audit_dir=tmp_path / "audit").call(
        "Delete it.", context(tmp_path), "dom1.a1", harvest="context/harness"
    )
    assert r.workspace is not None and not (r.workspace / "CLAUDE.md").exists()


def test_no_audit_dir_harvests_to_temp_and_never_caches(tmp_path, log):
    prop = Proposer(SPEC)
    r = prop.call("m", context(tmp_path), "x", harvest="context/harness")
    assert r.workspace is not None and (r.workspace / "CLAUDE.md").is_file()
    prop.call("m", context(tmp_path), "x", harvest="context/harness")
    assert len(calls(log)) == 2


def test_empty_reply_retries_then_raises(tmp_path, log, monkeypatch):
    apply_fake_env(monkeypatch, empty=True)
    prop = Proposer(SPEC, audit_dir=tmp_path / "audit", attempts=3)
    with pytest.raises(ProposerError) as ei:
        prop.call("m", context(tmp_path), "inv1.a1")
    assert ei.value.attempts == 3
    assert ei.value.cost_usd == pytest.approx(3 * 0.0123)
    assert "empty reply" in str(ei.value)
    assert len(calls(log)) == 3
    (failed,) = prop._calls
    assert not failed.ok and failed.attempts == 3
    # A failed call is audited but never served from the cache.
    with pytest.raises(ProposerError):
        prop.call("m", context(tmp_path), "inv1.a1", max_attempts=1)
    assert len(calls(log)) == 4


def test_is_error_raises(tmp_path, log, monkeypatch):
    apply_fake_env(monkeypatch, fail=True)
    prop = Proposer(SPEC, attempts=2)
    with pytest.raises(ProposerError) as ei:
        prop.call("m", None, "select.agg1")
    assert ei.value.attempts == 2
    assert ei.value.cost_usd == pytest.approx(0.02)
    assert "error_during_execution" in str(ei.value)
    assert "fake failure" in str(ei.value)


def test_zero_improver_budget_omits_max_budget_flag(tmp_path, log, monkeypatch):
    monkeypatch.setattr(config, "IMPROVER_MAX_BUDGET_USD", 0.0)
    Proposer(SPEC).call("m", None, "x")
    (rec,) = calls(log)
    assert "--max-budget-usd" not in rec["argv"]


def test_budget_cap_error_reports_spend_and_discards_edits(tmp_path, log, monkeypatch):
    # Claude Code checks --max-budget-usd between turns: a capped session overshoots, ends with
    # is_error/error_max_budget_usd and no `result`, and its edits are lost with it.
    monkeypatch.setattr(config, "IMPROVER_MAX_BUDGET_USD", 0.5)
    apply_fake_env(monkeypatch, budget=True, edit="CLAUDE.md=capped edit\n")
    prop = Proposer(SPEC, audit_dir=tmp_path / "audit", attempts=2)
    with pytest.raises(ProposerError) as ei:
        prop.call("m", context(tmp_path), "inv1.a1", harvest="context/harness")
    msg = str(ei.value)
    assert "error_max_budget_usd" in msg and "Reached maximum budget" in msg
    assert "session spent $1.60" in msg and "HARNESS2_IMPROVER_MAX_BUDGET_USD=0.5" in msg
    assert ei.value.attempts == 2 and ei.value.cost_usd == pytest.approx(3.2)
    assert not list((tmp_path / "audit").glob("*.workspace"))


def test_timeout_raises(tmp_path, log, monkeypatch):
    apply_fake_env(monkeypatch, hang=True)
    spec = ChannelSpec("claude_cli", "claude-opus-5-5", timeout=2)
    with pytest.raises(ProposerError, match="timeout after 2s") as ei:
        Proposer(spec).call("m", None, "x", max_attempts=1)
    assert ei.value.attempts == 1


def test_long_system_prompt_moves_to_stdin(tmp_path, log, monkeypatch):
    monkeypatch.setattr(P, "SYSTEM_ARG_MAX", 10)
    Proposer(SPEC).call("the mission", None, "x", system="a long system prompt")
    (rec,) = calls(log)
    assert rec["stdin"].startswith("# SYSTEM INSTRUCTIONS\n\na long system prompt")
    assert rec["stdin"].endswith("the mission")
    argv = rec["argv"]
    assert "SYSTEM INSTRUCTIONS" in argv[argv.index("--append-system-prompt") + 1]


def test_tools_fallback_when_cli_lacks_tools_flag(tmp_path, log, monkeypatch):
    apply_fake_env(monkeypatch, help_omit="--tools")
    Proposer(SPEC).call("m", None, "x")
    (rec,) = calls(log)
    argv = rec["argv"]
    assert "--tools" not in argv
    i = argv.index("--disallowedTools")
    assert argv[i + 1 : i + 5] == ["Bash", "WebFetch", "WebSearch", "Agent"]


def test_empty_model_rejected():
    with pytest.raises(ValueError):
        Proposer(ChannelSpec("claude_cli", ""))


def test_parse_json_tolerates_banners():
    assert _parse_json("") == {}
    assert _parse_json('{"result": "a"}') == {"result": "a"}
    assert _parse_json('banner line\n{"result": "b", "subtype": "success"}\n') == {
        "result": "b",
        "subtype": "success",
    }
    assert _parse_json("plain text reply") == {"result": "plain text reply"}
