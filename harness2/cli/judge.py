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

"""Judge the SELECTED run of each finished task in one namespace, and cache the rows.

Only the run the blind selector chose is scored: by the task's check.py when it has one,
else by a tool-less Claude session against rubric.json, else not at all. A judge failure is
recorded as ungraded, never as a zero, and a failed re-judge never replaces a graded row.

With --bases it scores the namespace's base runs instead (base_judged.json), so the
starting harness can be reported beside the adapted one.

Output: <runs>/adapt/<namespace>/judged.json, read by `harness2 report`.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from .. import config
from ..core import judge as J
from . import _common as C

_DERIVED = ("frac", "graded")


def add_parser(subparsers) -> argparse.ArgumentParser:
    p = subparsers.add_parser(
        "judge",
        help="score the chosen run of each finished task (check.py or rubric.json)",
        description=__doc__,
        formatter_class=C.HelpFormatter,
    )
    C.add_tasks_arg(p)
    C.add_runs_arg(p)
    C.add_k_arg(p)
    C.add_modes_arg(p)
    C.add_seed_arg(p)
    C.add_tag_arg(p)
    p.add_argument(
        "--bases",
        action="store_true",
        help="score the base runs into base_judged.json instead of the selected runs",
    )
    p.add_argument("--parallel", type=int, default=8, metavar="N", help="concurrent judge calls")
    p.set_defaults(func=run)
    return p


def load_rows(path: Path) -> list[J.Scored]:
    """Rebuild Scored rows from a judged.json written by core.judge.write."""
    if not path.is_file():
        return []
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"unreadable judged cache {path}: {exc}; move it aside to rebuild")
    rows = []
    for row in raw:
        row = {k: v for k, v in row.items() if k not in _DERIVED}
        if "mode" not in row and "arm" in row:
            row["mode"] = row.pop("arm")
        rows.append(J.Scored(**row))
    return rows


def _merge(old: list[J.Scored], new: list[J.Scored]) -> list[J.Scored]:
    """Per (task, mode, k): a changed chosen run replaces; for the same run_id the graded
    row wins, so a failed re-judge never erases an existing score."""
    by_key = {(r.task_id, r.mode, r.k): r for r in old}
    for r in new:
        key = (r.task_id, r.mode, r.k)
        prev = by_key.get(key)
        if (
            prev is not None
            and prev.run_id == r.run_id
            and prev.graded
            and not r.graded
            and not r.error.startswith("selection")
        ):
            continue
        by_key[key] = r
    return [by_key[k] for k in sorted(by_key)]


def _selection_record(sel: dict) -> dict:
    """A selection record keyed by `mode`; older summaries carry `arm` instead."""
    if "mode" not in sel and "arm" in sel:
        sel = dict(sel)
        sel["mode"] = sel.pop("arm")
    return sel


def read_summaries(ns_dir: Path) -> list[dict]:
    """Every readable task summary.json under one namespace, in task order."""
    out = []
    for summary_path in sorted(Path(ns_dir).glob("*/summary.json")):
        try:
            out.append(json.loads(summary_path.read_text(encoding="utf-8")))
        except (OSError, json.JSONDecodeError) as exc:
            print(f"  ! skipping unreadable {summary_path}: {exc}", flush=True)
    return out


def collect_selections(ns_dir: Path, modes: tuple[str, ...]) -> list[dict]:
    """Every requested mode's selection record from every finished task summary."""
    out: list[dict] = []
    for summary in read_summaries(ns_dir):
        for mode in modes:
            sel = (summary.get("selections") or {}).get(mode)
            if sel:
                out.append(_selection_record(sel))
    return out


def judge_bases(adapter, ns_dir: Path) -> int:
    """Score every base run recorded in this namespace's task summaries."""
    rows = []
    for summary in read_summaries(ns_dir):
        task_id = summary.get("task_id", "")
        for entry in summary.get("base") or []:
            if not isinstance(entry, dict) or not entry.get("run_dir"):
                continue
            label, run_dir = entry.get("label", ""), Path(entry["run_dir"])
            try:
                got = adapter.score(run_dir, task_id) if run_dir.is_dir() else None
            except Exception as exc:
                got = None
                print(f"  {task_id[:52]:<52} {label} ERROR {type(exc).__name__}: {exc}", flush=True)
            else:
                shown = "-" if got is None else f"{got.get('n_passed')}/{got.get('n_criteria')}"
                print(f"  {task_id[:52]:<52} {label} {shown}", flush=True)
            rows.append(
                {"task": task_id, "label": label, "run_id": entry.get("run_id", ""), "score": got}
            )
    if not rows:
        print(f"no base runs recorded under {ns_dir}; wrote nothing", flush=True)
        return 2
    out = ns_dir / "base_judged.json"
    out.write_text(json.dumps(rows, indent=1, default=str), encoding="utf-8")
    graded = sum(1 for r in rows if r["score"] is not None)
    print(f"wrote {out}: {len(rows)} row(s), {graded} graded")
    return 0 if graded == len(rows) else 2


def _kind(adapter, task_id: str) -> str | None:
    try:
        return adapter.judge_kind(task_id)
    except (OSError, ValueError):
        return None


def _needs_model_judge(adapter, task_ids) -> bool:
    return any(_kind(adapter, task_id) == "rubric" for task_id in task_ids)


def run(args: argparse.Namespace) -> int:
    if args.k < 1 or args.parallel < 1:
        raise ValueError("--k and --parallel must be positive")
    modes = C.parse_modes(args.modes)
    adapter = C.make_adapter(args.tasks, args.runs)
    runs = Path(adapter.spec.runs_root)
    ns = C.summary_namespace(args.k, seed=args.seed, tag=args.tag)
    ns_dir = C.runs_root_for(runs) / ns
    if not ns_dir.is_dir():
        raise FileNotFoundError(
            f"nothing to judge: {ns_dir} does not exist; has `harness2 run --k {args.k}"
            + C.flags_hint(seed=args.seed, tag=args.tag)
            + f"` run with --runs {runs}?"
        )
    identity = ns_dir / "config.json"
    if identity.is_file():
        recorded = json.loads(identity.read_text(encoding="utf-8")).get("judge")
        if recorded and recorded != config.JUDGE_MODEL:
            raise ValueError(
                f"this run was locked to judge model {recorded!r} but HARNESS2_JUDGE_MODEL is "
                f"{config.JUDGE_MODEL!r}; set it back or use a new --runs directory"
            )
    task_ids = sorted({s.get("task_id", "") for s in read_summaries(ns_dir)} - {""})
    config.validate(need_claude=_needs_model_judge(adapter, task_ids))

    if args.bases:
        return judge_bases(adapter, ns_dir)

    sels = collect_selections(ns_dir, modes)
    if not sels:
        raise RuntimeError(
            f"nothing to judge: no selections for modes "
            f"{[C.mode_label(m) for m in modes]} under {ns_dir} (tasks may still be running, "
            f"or the wrong --k/--seed/--tag/--mode was given)"
        )
    unjudgeable = sorted(
        {s.get("task_id", "") for s in sels if _kind(adapter, s.get("task_id", "")) is None}
    )
    if unjudgeable:
        print(
            f"  note: {len(unjudgeable)} task(s) have neither check.py nor rubric.json and stay "
            f"ungraded: {', '.join(unjudgeable[:5])}",
            flush=True,
        )

    print(f"judging {len(sels)} selection(s) under {ns} ...", flush=True)
    rows = J.judge_selections(adapter, sels, parallel=args.parallel)
    for r in sorted(rows, key=lambda r: (r.task_id, r.mode)):
        shown = f"{r.n_passed}/{r.n_criteria}" if r.graded else f"- ({r.error or 'ungraded'})"
        print(f"  {r.task_id[:48]:<48} {C.mode_label(r.mode):<10} {r.label:<6} {shown}")

    out_path = ns_dir / "judged.json"
    merged = _merge(load_rows(out_path), rows)
    J.write(merged, out_path)

    graded = sum(1 for r in merged if r.graded)
    print(
        f"judged.json: {len(merged)} row(s), {graded} graded, "
        f"{len(merged) - graded} ungraded (judge failure or no pick; NOT zeros)"
    )
    print(f"wrote {out_path}")
    print(
        f"next:  harness2 report --runs {runs} --k {args.k}"
        + C.flags_hint(seed=args.seed, tag=args.tag)
        + f" --mode {args.modes}"
    )
    return 0 if rows and all(r.graded for r in rows) else 2
