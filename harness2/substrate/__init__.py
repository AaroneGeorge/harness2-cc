"""Substrates: the agent framework a rollout runs under. Here only Claude Code."""

from __future__ import annotations

from .surface import CLAUDE_CODE_SURFACE, render, unrender

__all__ = ["CLAUDE_CODE_SURFACE", "ClaudeCodeSubstrate", "render", "unrender"]


def __getattr__(name: str):
    # Lazy, so this package imports even before (or without) the executor module.
    if name == "ClaudeCodeSubstrate":
        from .claude_code import ClaudeCodeSubstrate

        return ClaudeCodeSubstrate
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
