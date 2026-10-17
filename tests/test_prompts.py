"""Smoke check: every generated prompt speaks Claude Code only and names the runtime helper path."""

from __future__ import annotations

import pytest

from harness2.core import prompts as P
from harness2.core.spec import BenchPrompts

FORBIDDEN = ("OpenCode", "opencode", "Codex", "AGENTS.md", "plugin", "sub_agents", "Jinja", "{{")


def _all_prompts(pack: str) -> list[str]:
    bp = BenchPrompts(work="reconcile signed amounts in a transaction ledger", prompt_pack=pack)
    out: list[str] = []
    for k in (1, 2, 3):
        for i in range(1, k + 1):
            for dim in ("INV", "DOM"):
                out.append(P.propose(bp, dim, "claude_code", k=k, index=i))
                out.append(P.propose(bp, dim, "claude_code", k=k, index=i, round_t=2))
            out.append(P.aggregate(bp, "claude_code", k=k, index=i))
        out.append(P.compose_round(bp, "claude_code", t=2, k=k))
    out += [P.select(bp, 4, "mixed"), P.select(bp, 4, "finals"), P.experience(bp)]
    return out


@pytest.mark.parametrize("pack", ["adapt", "tts"])
def test_prompts_have_no_foreign_surface_strings(pack):
    for text in _all_prompts(pack):
        for bad in FORBIDDEN:
            assert bad not in text, bad


@pytest.mark.parametrize("pack", ["adapt", "tts"])
def test_proposal_prompts_name_the_runtime_skill_helper_path(pack):
    bp = BenchPrompts(work="reconcile signed amounts in a transaction ledger", prompt_pack=pack)
    for dim in ("INV", "DOM"):
        text = P.propose(bp, dim, "claude_code")
        assert ".claude/skills/<skill-name>/scripts/<file>.py" in text
