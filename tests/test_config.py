"""config: .env loading (shell wins) and validate() remediation errors."""

from __future__ import annotations

import os

import pytest

from harness2 import config


@pytest.fixture
def ok(monkeypatch):
    """A valid configuration with a `claude` binary that 'exists'."""
    for name, value in {
        "EXECUTOR_MODEL": "claude-opus-5-5",
        "EXECUTOR_EFFORT": "",
        "IMPROVER_MODEL": "claude-opus-5-5",
        "IMPROVER_EFFORT": "high",
        "IMPROVER_MAX_BUDGET_USD": 15.0,
        "JUDGE_MODEL": "claude-opus-5-5",
        "CLAUDE_BIN": "claude",
        "PHASE_WIDTH": 2,
        "PROPOSER_CONCURRENCY": 2,
        "TRANSPORT_ATTEMPTS": 2,
        "_PARSE_ERRORS": [],
    }.items():
        monkeypatch.setattr(config, name, value)
    monkeypatch.setattr(config.shutil, "which", lambda name: f"/usr/bin/{name}")
    return monkeypatch


def test_defaults_and_constants():
    assert config.METHOD_VERSION == 1
    assert config.EFFORT_LEVELS == ("low", "medium", "high", "xhigh", "max")
    assert config.DEFAULT_PROPOSER_CHANNEL == "claude_cli"
    assert config.PROPOSER_MODEL_BY_CHANNEL == {"claude_cli": config.IMPROVER_MODEL}
    assert isinstance(config.IMPROVER_MAX_BUDGET_USD, float)
    assert isinstance(config.RUNS_DIR, os.PathLike) and isinstance(config.TASKS_DIR, os.PathLike)
    assert (config.base_harness_dir() / "CLAUDE.md").is_file()
    assert issubclass(config.ConfigError, RuntimeError)


def test_valid_config_passes(ok):
    config.validate()
    ok.setattr(config, "EXECUTOR_EFFORT", "xhigh")
    config.validate()
    ok.setattr(config, "IMPROVER_MAX_BUDGET_USD", 0.0)  # 0 = no --max-budget-usd
    config.validate()


@pytest.mark.parametrize(
    "name, value, needle",
    [
        ("EXECUTOR_EFFORT", "turbo", "HARNESS2_EXECUTOR_EFFORT"),
        ("IMPROVER_EFFORT", "", "HARNESS2_IMPROVER_EFFORT"),
        ("PHASE_WIDTH", 0, "HARNESS2_PHASE_WIDTH"),
        ("PROPOSER_CONCURRENCY", -1, "HARNESS2_PROPOSER_CONCURRENCY"),
        ("TRANSPORT_ATTEMPTS", 0, "HARNESS2_TRANSPORT_ATTEMPTS"),
        ("IMPROVER_MAX_BUDGET_USD", -1.0, "HARNESS2_IMPROVER_MAX_BUDGET_USD"),
        ("EXECUTOR_MODEL", "anthropic/claude-opus-5-5", "bare Anthropic model id"),
        ("IMPROVER_MODEL", "vertex/claude", "HARNESS2_IMPROVER_MODEL"),
        ("JUDGE_MODEL", "a/b", "HARNESS2_JUDGE_MODEL"),
        ("_PARSE_ERRORS", ["HARNESS2_PHASE_WIDTH must be an integer"], "must be an integer"),
    ],
)
def test_validate_errors(ok, name, value, needle):
    ok.setattr(config, name, value)
    with pytest.raises(config.ConfigError, match=needle):
        config.validate(need_claude=False)


def test_missing_claude_binary(ok):
    ok.setattr(config.shutil, "which", lambda name: None)
    with pytest.raises(config.ConfigError, match="HARNESS2_CLAUDE_BIN"):
        config.validate()
    config.validate(need_claude=False)


def test_load_dotenv_shell_wins(tmp_path, monkeypatch):
    env = tmp_path / ".env"
    env.write_text(
        "# comment\n\n"
        "HARNESS2_TEST_A=from_file\n"
        "export HARNESS2_TEST_B='quoted value'\n"
        'HARNESS2_TEST_C="x # not a comment"\n'
        "HARNESS2_TEST_D=val # trailing comment\n"
        "not a line\n"
    )
    monkeypatch.setenv("HARNESS2_TEST_A", "from_shell")
    for k in "BCD":
        monkeypatch.delenv(f"HARNESS2_TEST_{k}", raising=False)
    config.load_dotenv(env)
    assert os.environ["HARNESS2_TEST_A"] == "from_shell"
    assert os.environ["HARNESS2_TEST_B"] == "quoted value"
    assert os.environ["HARNESS2_TEST_C"] == "x # not a comment"
    assert os.environ["HARNESS2_TEST_D"] == "val"
    config.load_dotenv(tmp_path / "missing.env")  # not an error


def test_env_helpers_collect_parse_errors(monkeypatch):
    monkeypatch.setattr(config, "_PARSE_ERRORS", [])
    monkeypatch.setenv("HARNESS2_TEST_INT", "two")
    monkeypatch.setenv("HARNESS2_TEST_EMPTY", "")
    assert config.env_int("HARNESS2_TEST_INT", 3) == 3
    assert config.env_float("HARNESS2_TEST_INT", 1.5) == 1.5
    assert config.env("HARNESS2_TEST_EMPTY", "dflt") == "dflt"
    assert len(config._PARSE_ERRORS) == 2
