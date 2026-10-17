"""Test doubles: a fake `claude` CLI and helpers to steer it."""

from __future__ import annotations

import json
import os
import stat
import sys
from pathlib import Path

FAKE_SCRIPT = Path(__file__).resolve().parent / "fake_claude.py"

_PREFIX = "FAKE_CLAUDE_"


def install_fake_claude(bin_dir: Path) -> Path:
    """Write an executable `claude` into `bin_dir` that runs the fake with this interpreter."""
    bin_dir = Path(bin_dir)
    bin_dir.mkdir(parents=True, exist_ok=True)
    exe = bin_dir / "claude"
    exe.write_text(f'#!/bin/sh\nexec "{sys.executable}" "{FAKE_SCRIPT}" "$@"\n', encoding="utf-8")
    exe.chmod(exe.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
    return exe


def fake_env(**overrides) -> dict[str, str]:
    """Environment variables steering the fake, from keyword arguments.

    `fake_env(reply="x", fail=True, log=path)` -> {"FAKE_CLAUDE_REPLY": "x",
    "FAKE_CLAUDE_FAIL": "1", "FAKE_CLAUDE_LOG": "<path>"}. True becomes "1", False/None
    drop the variable (value ""), lists are comma-joined, paths are stringified. A key that is
    already a full FAKE_CLAUDE_* name is used as is. Apply with `monkeypatch.setenv`, or
    pass to `apply_fake_env(monkeypatch, ...)`.
    """
    out: dict[str, str] = {}
    for key, value in overrides.items():
        name = key if key.startswith(_PREFIX) else _PREFIX + key.upper()
        if value is True:
            value = "1"
        elif value is False or value is None:
            value = ""
        elif isinstance(value, (list, tuple)):
            value = ",".join(str(v) for v in value)
        out[name] = str(value)
    return out


def apply_fake_env(monkeypatch, **overrides) -> dict[str, str]:
    """Set (or, for an empty value, unset) each fake_env() variable via monkeypatch."""
    env = fake_env(**overrides)
    for name, value in env.items():
        if value == "":
            monkeypatch.delenv(name, raising=False)
        else:
            monkeypatch.setenv(name, value)
    return env


def clear_fake_env(monkeypatch) -> None:
    for name in [k for k in os.environ if k.startswith(_PREFIX)]:
        monkeypatch.delenv(name, raising=False)


def calls(log_dir: Path) -> list[dict]:
    """The -p invocations the fake recorded under FAKE_CLAUDE_LOG, oldest first."""
    f = Path(log_dir) / "calls.jsonl"
    if not f.is_file():
        return []
    return [json.loads(line) for line in f.read_text(encoding="utf-8").splitlines() if line]


__all__ = [
    "FAKE_SCRIPT",
    "apply_fake_env",
    "calls",
    "clear_fake_env",
    "fake_env",
    "install_fake_claude",
]
