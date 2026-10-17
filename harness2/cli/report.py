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

"""Print the score report for one run from cached judged.json rows (read-only).

One score line per (mode, k) with pick provenance and the sanity warnings, the base-harness
line when `harness2 judge --bases` has run, and a per-task table: task, domain, mode, k,
chosen label, whether the chosen output is a base run, score, and improver spend in USD.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from ..core import judge as J
from . import _common as C
from .judge import load_rows, read_summaries


def add_parser(subparsers) -> argparse.ArgumentParser:
    p = subparsers.add_parser(
        "report",
        help="print scores from judged.json for a finished run",
        description=__doc__,
        formatter_class=C.HelpFormatter,
    )
    C.add_runs_arg(p)
    C.add_k_arg(p)
    C.add_modes_arg(p)
    C.add_seed_arg(p)
    C.add_tag_arg(p)
    p.set_defaults(func=run)
    return p


def _task_usd(summary: dict, mode: str) -> float:
    """Improver spend (stages + selection/reflection) for one task and mode; executor runs
    are billed per rollout and recorded in each rollout's result.json, not here."""
    from ..core import schedule as SCHED

    rec = SCHED._modes_of(summary).get(mode) or {}
    spend = sum(float(s.get("usd") or 0.0) for s in rec.get("stages") or [])
    sel = (summary.get("selections") or {}).get(mode) or {}
    return spend + float(sel.get("usd") or 0.0)


def _spend_and_fallbacks(summaries: list[dict], modes, k: int) -> tuple[dict, dict]:
    """(fell_back, usd) keyed (mode token, k), from the summaries the sweep checkpointed."""
    from ..core import schedule as SCHED

    fell_back: dict[tuple[str, int], int] = {}
    usd: dict[tuple[str, int], float] = {}
    for summary in summaries:
        records = SCHED._modes_of(summary)
        for mode in modes:
            key = (mode, k)
            rec = records.get(mode) or {}
            fell_back[key] = fell_back.get(key, 0) + len(rec.get("fell_back") or [])
            usd[key] = usd.get(key, 0.0) + _task_usd(summary, mode)
    return fell_back, usd


def _base_rows(path: Path, k: int) -> list[J.Scored]:
    if not path.is_file():
        return []
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise ValueError(f"unreadable {path}: {exc}") from None
    out = []
    for row in raw:
        score = row.get("score") or {}
        out.append(
            J.Scored(
                task_id=row.get("task", ""),
                mode="base",
                k=k,
                label=row.get("label", ""),
                run_id=row.get("run_id", ""),
                chosen_is_base=True,
                n_passed=score.get("n_passed"),
                n_criteria=score.get("n_criteria"),
                all_pass=bool(score.get("all_pass")),
                raw=score,
            )
        )
    return out


def run(args: argparse.Namespace) -> int:
    from ..core import report as R

    if args.k < 1:
        raise ValueError("--k must be positive")
    modes = C.parse_modes(args.modes)
    runs = C.path(args.runs)
    ns_dir = C.runs_root_for(runs) / C.summary_namespace(args.k, seed=args.seed, tag=args.tag)
    judged = ns_dir / "judged.json"
    rows = [r for r in load_rows(judged) if r.mode in modes]
    summaries = read_summaries(ns_dir) if ns_dir.is_dir() else []
    bases = _base_rows(ns_dir / "base_judged.json", args.k)
    if not rows and not bases:
        raise FileNotFoundError(
            f"nothing to report: no judged rows at {judged}; run `harness2 judge --runs {runs} "
            f"--k {args.k}{C.flags_hint(seed=args.seed, tag=args.tag)}` first"
        )

    title = (
        f"{C.BENCH}-{C.SUBSTRATE} k={args.k}"
        + (f" seed={args.seed}" if args.seed is not None else "")
        + f" modes={','.join(C.mode_label(m) for m in modes)}"
    )
    fell_back, usd = _spend_and_fallbacks(summaries, modes, args.k)
    if rows:
        print(R.render(R.summarise(rows, fell_back=fell_back, usd=usd), title=title))
    else:
        print(f"{title}\n  (no judged selections yet)")
    if bases:
        print("\nbase harness (every base run, before any adaptation)")
        for s in R.summarise(bases):
            print(s.line().replace(s.name, f"base k={s.k}", 1))

    by_key = {(r.task_id, r.mode): r for r in rows}
    table = []
    for summary in summaries:
        task_id = summary.get("task_id", "")
        for mode in modes:
            sel = (summary.get("selections") or {}).get(mode)
            if not sel:
                continue
            r = by_key.get((task_id, mode))
            score = "-"
            if r is not None and r.graded:
                score = f"{r.n_passed}/{r.n_criteria}"
            table.append(
                (
                    task_id,
                    summary.get("domain", ""),
                    C.mode_label(mode),
                    str(summary.get("k", args.k)),
                    sel.get("chosen_label") or "-",
                    "yes" if sel.get("chosen_is_base") else "no",
                    score,
                    f"{_task_usd(summary, mode):.2f}",
                )
            )
    if table:
        head = ("task", "domain", "mode", "k", "chosen", "base?", "score", "usd")
        widths = [max(len(h), *(len(row[i]) for row in table)) for i, h in enumerate(head)]
        print()
        print("  ".join(h.ljust(w) for h, w in zip(head, widths)).rstrip())
        for row in table:
            print("  ".join(c.ljust(w) for c, w in zip(row, widths)).rstrip())
    spend = sum(usd.values())
    if spend:
        print(
            f"\n  improver (editors, composer, selection, reflection) spend across reported "
            f"modes: ${spend:.2f}"
        )
    return 0
