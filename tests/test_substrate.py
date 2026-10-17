"""ClaudeCodeSubstrate against a fake `claude`: command, artifacts, trajectory, reuse, timeout."""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest
from fakes import apply_fake_env, calls

from harness2 import config
from harness2.core import runmark as RM
from harness2.substrate import ClaudeCodeSubstrate
from harness2.substrate.claude_code import render_stream
from harness2.tasks import Task

BASE = config.base_harness_dir()


def make_task(tmp_path: Path, **kw) -> Task:
    ws = tmp_path / "task" / "workspace"
    ws.mkdir(parents=True, exist_ok=True)
    (ws / "input.csv").write_text("a,b\n1,2\n")
    defaults = dict(
        id="demo/t1",
        domain="demo",
        dir=tmp_path / "task",
        instruction="Summarise input.csv into output/answer.md.",
        deliverables=["output/answer.md"],
        timeout_s=60,
        max_budget_usd=3.5,
        effort="",
        workspace=ws,
    )
    defaults.update(kw)
    return Task(**defaults)


def harness(tmp_path: Path, guard: str = "- Be careful.\n") -> Path:
    h = tmp_path / "harness"
    h.mkdir(exist_ok=True)
    (h / "CLAUDE.md").write_text("You are a careful analyst.\n")
    (h / "guardrails.md").write_text(guard)
    return h


@pytest.fixture
def log(tmp_path, monkeypatch, fake_claude):
    d = tmp_path / "fakelog"
    apply_fake_env(monkeypatch, log=d)
    return d


def test_execute_writes_command_stream_result_and_deliverables(tmp_path, log, monkeypatch):
    monkeypatch.setenv("CLAUDECODE", "1")
    monkeypatch.setenv("CLAUDE_CODE_ENTRYPOINT", "cli")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test-kept")
    sub = ClaudeCodeSubstrate(model="claude-opus-5-5", effort="high")
    task = make_task(tmp_path)
    run = sub.execute(task, harness(tmp_path), tmp_path / "run", base_harness=BASE)

    cmd = json.loads((run / "command.json").read_text())
    argv = cmd["argv"]
    assert argv[0] == config.CLAUDE_BIN
    for flag in ("-p", "--verbose", "--no-session-persistence"):
        assert flag in argv
    pairs = {
        "--output-format": "stream-json",
        "--permission-mode": "bypassPermissions",
        "--model": "claude-opus-5-5",
        "--effort": "high",
        "--setting-sources": "project",
        "--max-budget-usd": "3.5",
    }
    for flag, value in pairs.items():
        assert argv[argv.index(flag) + 1] == value
    assert "--max-turns" not in argv
    assert cmd["dropped_flags"] == []
    assert cmd["cwd"] == str(run / "workspace")

    ws = run / "workspace"
    assert (ws / "input.csv").is_file()
    assert "You are a careful analyst." in (ws / "CLAUDE.md").read_text()
    assert (ws / "output").is_dir()

    (rec,) = calls(log)
    assert rec["stdin"] == task.instruction
    assert Path(rec["cwd"]).resolve() == ws.resolve()
    assert "CLAUDECODE" not in rec["env_keys"]
    assert "CLAUDE_CODE_ENTRYPOINT" not in rec["env_keys"]
    assert "ANTHROPIC_API_KEY" in rec["env_keys"]

    lines = (run / "stream.jsonl").read_text().splitlines()
    assert json.loads(lines[0])["subtype"] == "init"
    result = json.loads((run / "result.json").read_text())
    assert result["subtype"] == "success" and result["is_error"] is False
    assert result["total_cost_usd"] == 0.05 and result["num_turns"] == 3
    assert (run / "stderr.txt").is_file()
    got = (run / "deliverables" / "output" / "answer.md").read_text()
    assert got == "fake deliverable for output/answer.md\n"

    mark = RM.read_mark(run)
    assert mark["harness"] == RM.fingerprint(tmp_path / "harness")
    assert mark["cost_usd"] == 0.05 and mark["num_turns"] == 3
    assert mark["subtype"] == "success" and mark["duration_ms"] == 10

    assert sub.delivered(run, task.deliverables)
    assert sub.deliverable_files(run, task.deliverables) == {"output/answer.md": got}
    assert sub.deliverable_text(run, task.deliverables) == f"# output/answer.md\n\n{got}"


def test_task_effort_overrides_and_empty_effort_omits_flag(tmp_path, fake_claude):
    sub = ClaudeCodeSubstrate(model="m", effort="")
    argv, dropped = sub.command(make_task(tmp_path))
    assert "--effort" not in argv and dropped == []
    argv, _ = sub.command(make_task(tmp_path, effort="max"))
    assert argv[argv.index("--effort") + 1] == "max"


def test_zero_task_budget_omits_max_budget_flag(tmp_path, fake_claude):
    sub = ClaudeCodeSubstrate(model="m", effort="")
    argv, dropped = sub.command(make_task(tmp_path, max_budget_usd=0.0))
    assert "--max-budget-usd" not in argv and dropped == []


def test_flags_missing_from_help_are_dropped_and_recorded(tmp_path, log, monkeypatch):
    apply_fake_env(monkeypatch, help_omit="--effort,--setting-sources")
    sub = ClaudeCodeSubstrate(model="m", effort="high")
    run = sub.execute(make_task(tmp_path), None, tmp_path / "run", base_harness=BASE)
    cmd = json.loads((run / "command.json").read_text())
    assert cmd["dropped_flags"] == ["--effort", "--setting-sources"]
    assert "--effort" not in cmd["argv"] and "--setting-sources" not in cmd["argv"]
    assert json.loads((run / "result.json").read_text())["subtype"] == "success"


def test_delivered_false_when_missing_or_empty(tmp_path, log, monkeypatch):
    sub = ClaudeCodeSubstrate(model="m")
    apply_fake_env(monkeypatch, deliver="output/other.md")
    task = make_task(tmp_path, deliverables=["output/answer.md"])
    run = sub.execute(task, None, tmp_path / "run", base_harness=BASE)
    assert not sub.delivered(run, task.deliverables)
    assert sub.deliverable_files(run, task.deliverables) == {}
    assert sub.deliverable_text(run, task.deliverables) == ""

    apply_fake_env(monkeypatch, deliver="output/answer.md")
    run2 = sub.execute(task, None, tmp_path / "run2", base_harness=BASE)
    assert sub.delivered(run2, task.deliverables)
    (run2 / "deliverables" / "output" / "answer.md").write_text("")
    assert not sub.delivered(run2, task.deliverables)
    assert not sub.delivered(run2, [])


def test_binary_deliverable_is_summarised(tmp_path):
    run = tmp_path / "run"
    (run / "deliverables" / "output").mkdir(parents=True)
    (run / "deliverables" / "output" / "a.bin").write_bytes(b"\x00\x01\x02")
    (run / "deliverables" / "output" / "b.md").write_bytes(b"caf\xe9\n")
    files = ClaudeCodeSubstrate(model="m").deliverable_files(run, ["output/a.bin", "output/b.md"])
    assert files["output/a.bin"] == "(binary file, 3 bytes)"
    assert files["output/b.md"] == "caf�\n"


def test_trajectory_grammar_and_clipping(tmp_path, log):
    sub = ClaudeCodeSubstrate(model="m")
    run = sub.execute(make_task(tmp_path), None, tmp_path / "run", base_harness=BASE)
    text = sub.trajectory(run, obs_trunc=50_000)
    assert "[ASSISTANT] I will read the task and write the deliverable." in text
    assert "[ACTION] Write(" in text and '"content": "draft"' in text
    assert "[OBSERVATION] File created successfully at:" in text
    assert "[OBSERVATION ERROR] cat: missing.txt: No such file or directory" in text
    assert "[ACTION] Bash(" in text
    assert text.rstrip().splitlines()[-1] == (
        "[RESULT] subtype=success turns=3 cost_usd=0.05 duration_s=0.0"
    )
    assert "[THINKING]" not in text  # empty thinking blocks are skipped
    assert "rate_limit" not in text and "init" not in text
    assert (run / "trajectory.md").read_text() == text

    clipped = sub.trajectory(run, obs_trunc=12)
    assert "render truncation)" in clipped
    assert "[OBSERVATION] File created…(+" in clipped
    assert (run / "trajectory.md").read_text() == clipped

    capped = sub.trajectory(run, obs_trunc=50_000, max_chars=100)
    body, marker = capped.rsplit("\n", 1)
    assert len(body) == 100 and marker.startswith("…(+") and marker.endswith("truncation)")


def test_render_stream_is_defensive(tmp_path):
    p = tmp_path / "stream.jsonl"
    events = [
        {"type": "system", "subtype": "init"},
        {"type": "mystery", "payload": 1},
        {"type": "assistant", "message": {"content": "not a list"}},
        {
            "type": "assistant",
            "message": {
                "content": [
                    {"type": "thinking", "thinking": "Plan the work."},
                    {"type": "tool_use", "name": "Read", "input": {"file_path": "x" * 1000}},
                ]
            },
        },
        {
            "type": "user",
            "message": {
                "content": [
                    {
                        "type": "tool_result",
                        "content": [
                            {"type": "text", "text": "one"},
                            {"type": "image", "source": {}},
                        ],
                    },
                ]
            },
        },
        {"type": "user", "message": {"content": "a plain prompt string"}},
        {
            "type": "result",
            "subtype": "error_max_budget_usd",
            "is_error": True,
            "errors": ["Reached maximum budget ($0.5)"],
            "total_cost_usd": 1.6,
            "num_turns": 3,
            "duration_ms": 6370,
        },
    ]
    p.write_text("\n".join(json.dumps(e) for e in events) + "\n{broken json\n[1, 2]\n")
    text = render_stream(p, obs_trunc=1000, max_chars=10_000)
    lines = text.splitlines()
    assert lines[0] == "[THINKING] Plan the work."
    assert lines[1].startswith("[ACTION] Read(") and "render truncation)" in lines[1]
    assert "[OBSERVATION] one\n(image)" in text
    assert "plain prompt string" not in text and "mystery" not in text
    assert "[ERROR] error_max_budget_usd: Reached maximum budget ($0.5)" in text
    assert "[RESULT] subtype=error_max_budget_usd turns=3 cost_usd=1.6 duration_s=6.4" in text
    assert "[ERROR] unparsable stream line: {broken json" in text
    assert render_stream(tmp_path / "nope.jsonl", obs_trunc=10, max_chars=10).startswith(
        "(failed to read trajectory"
    )
    (tmp_path / "empty.jsonl").write_text("")
    assert render_stream(tmp_path / "empty.jsonl", obs_trunc=10, max_chars=10) == (
        "(no parseable events in trajectory)"
    )


def test_idempotent_reuse_and_fingerprint_mismatch(tmp_path, log):
    sub = ClaudeCodeSubstrate(model="m")
    task = make_task(tmp_path)
    h = harness(tmp_path)
    run = sub.execute(task, h, tmp_path / "run", base_harness=BASE)
    assert len(calls(log)) == 1
    assert sub.execute(task, h, tmp_path / "run", base_harness=BASE) == run
    assert len(calls(log)) == 1  # reused, not re-run

    (h / "guardrails.md").write_text("- A different rule.\n")
    sub.execute(task, h, tmp_path / "run", base_harness=BASE)
    assert len(calls(log)) == 2  # harness changed: re-rolled
    assert RM.read_mark(run)["harness"] == RM.fingerprint(h)
    assert "A different rule." in (run / "workspace" / "CLAUDE.md").read_text()

    base_run = sub.execute(task, None, tmp_path / "base", base_harness=BASE)
    assert RM.read_mark(base_run)["harness"] == "base"
    sub.execute(task, None, tmp_path / "base", base_harness=BASE)
    assert len(calls(log)) == 3


def test_timeout_is_recorded_not_raised(tmp_path, log, monkeypatch):
    apply_fake_env(monkeypatch, hang=True)
    sub = ClaudeCodeSubstrate(model="m")
    task = make_task(tmp_path)
    run = sub.execute(task, None, tmp_path / "run", base_harness=BASE, timeout=2)
    result = json.loads((run / "result.json").read_text())
    assert result["is_error"] is True and result["subtype"] == "timeout"
    assert RM.is_complete(run, harness_dir=None)
    assert RM.read_mark(run)["subtype"] == "timeout"
    assert not sub.delivered(run, task.deliverables)
    text = sub.trajectory(run, obs_trunc=50_000)
    assert "[ERROR] timeout: killed after the 2s wall timeout" in text
    assert "[RESULT] subtype=timeout" in text
    (rec,) = calls(log)
    assert rec["pgid"] == rec["pid"]  # the CLI ran in its own process group ...
    with pytest.raises(ProcessLookupError):
        os.killpg(rec["pgid"], 0)  # ... and nothing of it survived the kill


def test_cli_error_result_is_recorded(tmp_path, log, monkeypatch):
    apply_fake_env(monkeypatch, fail=True)
    sub = ClaudeCodeSubstrate(model="m")
    run = sub.execute(make_task(tmp_path), None, tmp_path / "run", base_harness=BASE)
    result = json.loads((run / "result.json").read_text())
    assert result["is_error"] is True and result["subtype"] == "error_during_execution"
    assert "[ERROR] error_during_execution: fake failure" in sub.trajectory(run, obs_trunc=10_000)
    assert RM.is_complete(run, harness_dir=None)


def test_dead_launch_raises_and_is_not_marked(tmp_path, fake_claude):
    sub = ClaudeCodeSubstrate(model="m", effort="high")
    task = make_task(tmp_path)
    # A CLI that rejects an option exits before any event: not evidence about the harness.
    sub.command = lambda t: ([fake_claude, "-p", "--bogus"], [])  # type: ignore[method-assign]
    with pytest.raises(RuntimeError, match="no stream events"):
        sub.execute(task, None, tmp_path / "run", base_harness=BASE)
    assert not RM.is_complete(tmp_path / "run", harness_dir=None)


def test_preflight(tmp_path, fake_claude, monkeypatch):
    ClaudeCodeSubstrate().preflight()
    with pytest.raises(config.ConfigError, match="not found on PATH"):
        ClaudeCodeSubstrate(claude_bin=str(tmp_path / "no-such-claude")).preflight()


def test_surface_name():
    sub = ClaudeCodeSubstrate(model="m")
    assert sub.name == "cc" and sub.surface().name == "claude_code"
