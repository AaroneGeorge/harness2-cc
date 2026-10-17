# Copyright 2026 Google LLC
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     https://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.
# Modifications Copyright 2026 harness2-claude-code contributors.

"""Measurement of the run that selection chose, after it chose it.

Nothing in the method imports this module. A judge failure is None, never a zero, and a None is
excluded from the denominator. `rubric_judge` is the model-graded path for a task that ships
criteria but no deterministic check.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import tempfile
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from pathlib import Path
from typing import Sequence

from .spec import BenchmarkAdapter


@dataclass
class Scored:
    task_id: str
    mode: str
    k: int
    label: str
    run_id: str
    chosen_is_base: bool
    n_passed: int | None = None
    n_criteria: int | None = None
    all_pass: bool = False
    raw: dict = field(default_factory=dict)
    error: str = ""

    @property
    def graded(self) -> bool:
        """Whether the judge succeeded, not whether the run did well."""
        return self.n_criteria is not None and self.n_criteria > 0

    @property
    def frac(self) -> float | None:
        return (self.n_passed / self.n_criteria) if self.graded else None


def _mode_of(sel: dict) -> str:
    """The mode token of a selection record, reading the legacy key too."""
    return str(sel.get("mode") if "mode" in sel else sel.get("arm") or "")


def judge_one(
    adapter: BenchmarkAdapter,
    task_id: str,
    mode: str,
    k: int,
    label: str,
    run_id: str,
    run_dir: Path,
    *,
    chosen_is_base: bool = False,
) -> Scored:
    s = Scored(
        task_id=task_id, mode=mode, k=k, label=label, run_id=run_id, chosen_is_base=chosen_is_base
    )
    try:
        got = adapter.score(run_dir, task_id)
    except Exception as exc:
        s.error = f"{type(exc).__name__}: {exc}"
        return s
    if not got:
        s.error = "judge returned nothing"
        return s
    s.raw = dict(got)
    s.n_passed = got.get("n_passed")
    s.n_criteria = got.get("n_criteria")
    s.all_pass = bool(got.get("all_pass", s.graded and s.n_passed == s.n_criteria))
    return s


def judge_selections(
    adapter: BenchmarkAdapter, selections: Sequence[dict], *, parallel: int = 8
) -> list[Scored]:
    """Judge each selection's chosen run, given Selection.to_dict() records."""
    out: list[Scored] = []
    if not selections:
        return out
    with ThreadPoolExecutor(max_workers=parallel) as pool:
        futs = {}
        for sel in selections:
            rd = sel.get("chosen_run_dir")
            if not sel.get("ok") or not sel.get("chosen_label") or not rd:
                out.append(
                    Scored(
                        task_id=sel.get("task_id", ""),
                        mode=_mode_of(sel),
                        k=sel.get("k", 0),
                        label=sel.get("chosen_label", ""),
                        run_id=sel.get("chosen_run_id", ""),
                        chosen_is_base=bool(sel.get("chosen_is_base")),
                        error="selection did not complete successfully",
                    )
                )
                continue
            futs[
                pool.submit(
                    judge_one,
                    adapter,
                    sel["task_id"],
                    _mode_of(sel),
                    sel["k"],
                    sel["chosen_label"],
                    sel.get("chosen_run_id", ""),
                    Path(rd),
                    chosen_is_base=bool(sel.get("chosen_is_base")),
                )
            ] = sel
        for f in as_completed(futs):
            try:
                out.append(f.result())
            except Exception as exc:
                sel = futs[f]
                out.append(
                    Scored(
                        task_id=sel.get("task_id", ""),
                        mode=_mode_of(sel),
                        k=sel.get("k", 0),
                        label=sel.get("chosen_label", ""),
                        run_id=sel.get("chosen_run_id", ""),
                        chosen_is_base=bool(sel.get("chosen_is_base")),
                        error=f"{type(exc).__name__}: {exc}",
                    )
                )
    return out


def write(rows: Sequence[Scored], path: Path | str) -> Path:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(
            [r.__dict__ | {"frac": r.frac, "graded": r.graded} for r in rows], indent=2, default=str
        ),
        encoding="utf-8",
    )
    return path


_DROP_ENV = ("CLAUDECODE", "CLAUDE_CODE_ENTRYPOINT")
_FENCED_JSON = re.compile(r"```json\s*\n(.*?)\n?\s*```", re.S)


def _parse_cli_json(stdout: str) -> dict:
    """The claude CLI's one JSON object, tolerating banners and trailing noise around it."""
    s = (stdout or "").strip()
    if not s:
        return {}
    try:
        return json.loads(s)
    except json.JSONDecodeError:
        for line in reversed(s.splitlines()):
            line = line.strip()
            if line.startswith("{") and line.endswith("}"):
                try:
                    return json.loads(line)
                except json.JSONDecodeError:
                    continue
        i, j = s.find("{"), s.rfind("}")
        if 0 <= i < j:
            try:
                return json.loads(s[i : j + 1])
            except json.JSONDecodeError:
                pass
    return {"result": s}


def _rubric_prompt(deliverable_text: str, instruction: str, criteria: Sequence[str]) -> str:
    numbered = "\n".join(f"{i}. {c.strip()}" for i, c in enumerate(criteria, 1))
    return (
        "You are checking one piece of finished work against a fixed list of criteria. You "
        "have no tools; everything you need is in this message.\n\n"
        "# THE REQUEST THE WORK WAS DONE FOR\n\n"
        f"{instruction.strip()}\n\n"
        "# THE DELIVERED WORK\n\n"
        f"{deliverable_text.strip() or '(nothing was delivered)'}\n\n"
        "# THE CRITERIA\n\n"
        f"{numbered}\n\n"
        "Decide each criterion independently and strictly: it passes only when the delivered "
        "work visibly satisfies it. Missing, partial, or unverifiable counts as a fail.\n\n"
        "# OUTPUT FORMAT\n\n"
        "First your reasoning, briefly, criterion by criterion. Then, as the very last thing, "
        f"one fenced json block with exactly {len(criteria)} results, in the order above:\n\n"
        "```json\n"
        '{"results": [{"criterion": "<the criterion text>", "pass": true, "why": "<one line>"}]}\n'
        "```"
    )


def _rubric_results(text: str, criteria: Sequence[str]) -> list[dict] | None:
    """The per-criterion verdicts from the last fenced json block, or None if malformed."""
    blocks = _FENCED_JSON.findall(text or "")
    if not blocks:
        return None
    try:
        doc = json.loads(blocks[-1])
    except json.JSONDecodeError:
        return None
    results = doc.get("results") if isinstance(doc, dict) else None
    if not isinstance(results, list) or len(results) != len(criteria):
        return None
    out: list[dict] = []
    for crit, row in zip(criteria, results):
        if not isinstance(row, dict):
            return None
        verdict = row.get("pass")
        if isinstance(verdict, str) and verdict.strip().lower() in ("true", "false"):
            verdict = verdict.strip().lower() == "true"
        if not isinstance(verdict, bool):
            return None
        out.append({"criterion": crit, "pass": verdict, "why": str(row.get("why") or "")})
    return out


def rubric_judge(
    deliverable_text: str,
    instruction: str,
    criteria: list[str],
    *,
    model: str,
    claude_bin: str = "claude",
    timeout: int = 600,
) -> dict | None:
    """Grade a deliverable against criteria in one tool-less `claude -p` session.

    Returns {"n_passed", "n_criteria", "all_pass", "per_criterion", "metric"}, or None on ANY
    failure -- a failed judge is never a zero. Never raises.
    """
    try:
        crits = [str(c) for c in (criteria or []) if str(c).strip()]
        if not crits:
            return None
        cmd = [
            claude_bin,
            "-p",
            "--output-format",
            "json",
            "--model",
            model,
            "--tools",
            "",
            "--setting-sources",
            "project",
            "--no-session-persistence",
        ]
        env = {k: v for k, v in os.environ.items() if k not in _DROP_ENV}
        with tempfile.TemporaryDirectory(prefix="harness2-judge-") as cwd:
            p = subprocess.run(
                cmd,
                input=_rubric_prompt(deliverable_text or "", instruction or "", crits),
                capture_output=True,
                text=True,
                cwd=cwd,
                env=env,
                timeout=timeout,
            )
        payload = _parse_cli_json(p.stdout)
        if payload.get("is_error") or payload.get("subtype") not in (None, "success"):
            return None
        text = str(payload.get("result") or payload.get("text") or "")
        rows = _rubric_results(text, crits)
        if rows is None:
            return None
        n_passed = sum(1 for r in rows if r["pass"])
        return {
            "n_passed": n_passed,
            "n_criteria": len(rows),
            "all_pass": n_passed == len(rows),
            "per_criterion": rows,
            "metric": "criteria",
        }
    except Exception:
        return None
