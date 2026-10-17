"""Settings for Harness² on Claude Code. Shell values override `.env` in the current directory.

Stdlib only. Every value is optional; `validate()` turns a bad one into a `ConfigError` whose
message is a one-line remediation.
"""

from __future__ import annotations

import os
import shutil
from pathlib import Path

PKG = Path(__file__).resolve().parent
METHOD_VERSION = 1

EFFORT_LEVELS = ("low", "medium", "high", "xhigh", "max")


class ConfigError(RuntimeError):
    """A deployment value is unusable; the message carries the remediation."""


def load_dotenv(path: Path | str | None = None) -> None:
    """Load KEY=VALUE lines from `path` (default `./.env`) into os.environ with setdefault,
    so the shell wins.

    Accepts blank lines, `#` comments, an optional `export ` prefix, and single- or
    double-quoted values. A missing or unreadable file is not an error.
    """
    try:
        text = Path(path if path is not None else Path.cwd() / ".env").read_text(encoding="utf-8")
    except OSError:
        return
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        if line.startswith("export "):
            line = line[len("export ") :].lstrip()
        key, _, value = line.partition("=")
        key = key.strip()
        if not key or not all(c.isalnum() or c == "_" for c in key):
            continue
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in ("'", '"'):
            value = value[1:-1]
        elif " #" in value:
            value = value.split(" #", 1)[0].rstrip()
        os.environ.setdefault(key, value)


load_dotenv()

# Parse problems are collected here and reported by validate(), never raised at import time,
# so `harness2 --help` works with a broken .env.
_PARSE_ERRORS: list[str] = []


def env(name: str, default=None):
    """Env override where a set-but-empty variable is the same as an unset one."""
    return os.environ.get(name) or default


def env_int(name: str, default: int) -> int:
    v = env(name)
    if v is None:
        return int(default)
    try:
        return int(v)
    except ValueError:
        _PARSE_ERRORS.append(f"{name} must be an integer, got {v!r}; fix it in .env or the shell")
        return int(default)


def env_float(name: str, default: float) -> float:
    v = env(name)
    if v is None:
        return float(default)
    try:
        return float(v)
    except ValueError:
        _PARSE_ERRORS.append(f"{name} must be a number, got {v!r}; fix it in .env or the shell")
        return float(default)


EXECUTOR_MODEL = env("HARNESS2_EXECUTOR_MODEL", "claude-opus-5-5")
EXECUTOR_EFFORT = env("HARNESS2_EXECUTOR_EFFORT", "")
IMPROVER_MODEL = env("HARNESS2_IMPROVER_MODEL", "claude-opus-5-5")
IMPROVER_EFFORT = env("HARNESS2_IMPROVER_EFFORT", "high")
# 0 means "do not pass --max-budget-usd" to improver sessions.
IMPROVER_MAX_BUDGET_USD = env_float("HARNESS2_IMPROVER_MAX_BUDGET_USD", 25.0)
JUDGE_MODEL = env("HARNESS2_JUDGE_MODEL", "claude-opus-5-5")
RUNS_DIR = Path(env("HARNESS2_RUNS_DIR", "runs")).expanduser()
TASKS_DIR = Path(env("HARNESS2_TASKS_DIR", "tasks")).expanduser()
CLAUDE_BIN = env("HARNESS2_CLAUDE_BIN", "claude")
PHASE_WIDTH = env_int("HARNESS2_PHASE_WIDTH", 2)
PROPOSER_CONCURRENCY = env_int("HARNESS2_PROPOSER_CONCURRENCY", 2)
TRANSPORT_ATTEMPTS = env_int("HARNESS2_TRANSPORT_ATTEMPTS", 2)

DEFAULT_PROPOSER_CHANNEL = "claude_cli"
PROPOSER_MODEL_BY_CHANNEL = {"claude_cli": IMPROVER_MODEL}


def base_harness_dir() -> Path:
    """The v0 harness tree shipped inside the package."""
    return PKG / "base_harness"


def validate(*, need_claude: bool = True) -> None:
    """Raise ConfigError (one-line remediation) for the first unusable setting."""
    if _PARSE_ERRORS:
        raise ConfigError(_PARSE_ERRORS[0])
    if EXECUTOR_EFFORT and EXECUTOR_EFFORT not in EFFORT_LEVELS:
        raise ConfigError(
            f"HARNESS2_EXECUTOR_EFFORT={EXECUTOR_EFFORT!r} is not an effort level; use one of "
            f"{', '.join(EFFORT_LEVELS)} or leave it empty for the Claude Code default"
        )
    if IMPROVER_EFFORT not in EFFORT_LEVELS:
        raise ConfigError(
            f"HARNESS2_IMPROVER_EFFORT={IMPROVER_EFFORT!r} is not an effort level; use one of "
            f"{', '.join(EFFORT_LEVELS)}"
        )
    for var, value in (
        ("HARNESS2_PHASE_WIDTH", PHASE_WIDTH),
        ("HARNESS2_PROPOSER_CONCURRENCY", PROPOSER_CONCURRENCY),
        ("HARNESS2_TRANSPORT_ATTEMPTS", TRANSPORT_ATTEMPTS),
    ):
        if value < 1:
            raise ConfigError(f"{var}={value} must be a positive integer; set it to 1 or more")
    if IMPROVER_MAX_BUDGET_USD < 0:
        raise ConfigError(
            f"HARNESS2_IMPROVER_MAX_BUDGET_USD={IMPROVER_MAX_BUDGET_USD} must not be negative; "
            f"set a per-session cap in USD such as 25, or 0 for no cap"
        )
    for var, model in (
        ("HARNESS2_EXECUTOR_MODEL", EXECUTOR_MODEL),
        ("HARNESS2_IMPROVER_MODEL", IMPROVER_MODEL),
        ("HARNESS2_JUDGE_MODEL", JUDGE_MODEL),
    ):
        if "/" in model:
            raise ConfigError(
                f"{var}={model!r} must be a bare Anthropic model id such as 'claude-opus-5-5' "
                f"(no provider prefix)"
            )
    if need_claude and shutil.which(CLAUDE_BIN) is None:
        raise ConfigError(
            f"Claude Code CLI {CLAUDE_BIN!r} not found on PATH; install Claude Code and log in, "
            f"or set HARNESS2_CLAUDE_BIN to its path"
        )
