"""Shared fixtures. No test may call the real `claude` binary or the network."""

from __future__ import annotations

import os

import pytest
from fakes import clear_fake_env, install_fake_claude

from harness2 import config


@pytest.fixture
def fake_claude(tmp_path_factory, monkeypatch):
    """Put a fake `claude` first on PATH, point config.CLAUDE_BIN at it, and return its path.

    The fake is steered by FAKE_CLAUDE_* variables (see tests/fakes); any inherited from the
    outer environment are cleared. Retry back-off in the proposer is disabled.
    """
    exe = install_fake_claude(tmp_path_factory.mktemp("fakebin"))
    clear_fake_env(monkeypatch)
    monkeypatch.setenv("PATH", f"{exe.parent}{os.pathsep}{os.environ.get('PATH', '')}")
    monkeypatch.setattr(config, "CLAUDE_BIN", str(exe))
    monkeypatch.setattr("harness2.core.proposer.RETRY_BACKOFF_S", 0)
    return str(exe)
