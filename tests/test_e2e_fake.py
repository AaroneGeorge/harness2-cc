"""End to end through `harness2.cli.main` with the fake `claude`: run -> judge -> report ->
library show -> export -> resumed run. No real `claude`, no network."""

from __future__ import annotations

import json
from pathlib import Path

from fakes import apply_fake_env, calls

from harness2 import tasks
from harness2.cli import main

EDIT = "Reconcile every row before you write the answer."
# One reply serves every improver-side role: the proposer's <notes>, and the reflection's
# <experience> block that turns an adopted harness into a library entry.
IMPROVER_REPLY = (
    "<notes>Added a reconcile-first rule to CLAUDE.md.</notes>\n"
    "<experience>\n"
    "cue: a ledger or contact list must be reconciled\n"
    "lesson: reconcile every row before writing the answer\n"
    "evidence: base1 wrote the answer without checking the rows\n"
    "confidence: medium\n"
    "</experience>"
)
JUDGE_REPLY = (
    "Criterion 1 holds, criterion 2 does not.\n"
    "```json\n"
    '{"results": [{"criterion": "a", "pass": true, "why": "x"}, '
    '{"criterion": "b", "pass": false, "why": "y"}]}\n'
    "```"
)
SLUGS = ("data-cleanup__contacts", "writing__notes")


def _tasks(root: Path) -> Path:
    for domain, name in (
        ("data-cleanup", "contacts"),
        ("data-cleanup", "ledger"),
        ("writing", "notes"),
    ):
        d = tasks.scaffold(root, domain, name, instruction=f"Do the {name} task.")
        (d / "rubric.json").write_text(json.dumps({"criteria": ["has a total", "is short"]}))
    return root


def test_run_judge_report_library_export_resume(tmp_path, fake_claude, monkeypatch, capsys):
    log = tmp_path / "fakelog"
    apply_fake_env(monkeypatch, log=log, reply=IMPROVER_REPLY, edit=f"CLAUDE.md={EDIT}\n")
    tasks_root = _tasks(tmp_path / "tasks")
    runs = tmp_path / "runs"
    run_args = ["run", "--tasks", str(tasks_root), "--runs", str(runs), "--k", "1"]
    run_args += ["--mode", "both", "--max-tasks", "1"]

    # 1. run: k=1, both modes, the first task of each of the two domains.
    assert main(run_args) == 0, capsys.readouterr()
    adapt = runs / "adapt"
    for slug in SLUGS:
        summary = json.loads((adapt / "local-cc-agg1" / slug / "summary.json").read_text())
        assert summary["status"] == "ok"
        for mode in ("agg", "seq"):
            ledger = summary["modes"][mode]["ledger"]
            assert (ledger["R"], ledger["P"]) == (4, 3), (slug, mode, ledger)
            assert summary["selections"][mode]["ok"]
        sel_agg = adapt / "local-cc-agg1" / slug / "selection" / "agg1" / "selection.json"
        sel_seq = adapt / "local-cc-seq1" / slug / "selection" / "seq1" / "selection.json"
        assert sel_agg.is_file() and sel_seq.is_file()
        assert json.loads(sel_agg.read_text())["chosen_label"] == "agg1"
    assert (adapt / "local-cc-agg1" / "config.json").is_file()
    base1 = runs / "rollouts" / "local-cc-agg1" / "data-cleanup" / "contacts" / "base1"
    for name in ("trajectory.md", "result.json", "stream.jsonl", ".adapt_complete"):
        assert (base1 / name).is_file(), name
    lib = runs / "library" / "local" / "k1-cc" / "agg" / "data-cleanup"
    assert (lib / "INDEX.md").is_file()
    assert (lib / "harnesses" / "data-cleanup__contacts" / "EXPERIENCE.md").is_file()
    n_calls = len(calls(log))
    capsys.readouterr()

    # 2. judge: the rubric judge grades from the last fenced json block.
    apply_fake_env(monkeypatch, reply=JUDGE_REPLY)
    common = ["--tasks", str(tasks_root), "--runs", str(runs), "--k", "1", "--mode", "both"]
    assert main(["judge", *common]) == 0
    rows = json.loads((adapt / "local-cc-agg1" / "judged.json").read_text())
    assert len(rows) == 4
    assert all(r["graded"] and (r["n_passed"], r["n_criteria"]) == (1, 2) for r in rows)
    assert {r["mode"] for r in rows} == {"agg", "seq"}
    assert main(["judge", *common, "--bases"]) == 0
    assert (adapt / "local-cc-agg1" / "base_judged.json").is_file()
    capsys.readouterr()

    # 3. report
    assert main(["report", "--runs", str(runs), "--k", "1", "--mode", "both"]) == 0
    out = capsys.readouterr().out
    assert "score  50.00%" in out and "data-cleanup/contacts" in out and "base k=1" in out

    # 4. library show
    assert main(["library", "show", "--runs", str(runs), "--k", "1"]) == 0
    out = capsys.readouterr().out
    assert "INDEX.md" in out and "data-cleanup__contacts" in out

    # 5. export the adopted parallel harness into a project
    dest = tmp_path / "project"
    exp = ["export", "--runs", str(runs), "--task", "data-cleanup/contacts", "--k", "1"]
    assert main([*exp, "--dest", str(dest)]) == 0
    assert EDIT in (dest / "CLAUDE.md").read_text()
    assert main([*exp, "--dest", str(dest)]) == 1  # refuses to overwrite without --force
    capsys.readouterr()

    # 6. the same run again resumes: every task is skipped and no model is called.
    apply_fake_env(monkeypatch, reply=IMPROVER_REPLY)
    before = len(calls(log))
    assert before == n_calls + 4 + 2  # the rubric judge: 4 selected runs + 2 base runs
    assert main(run_args) == 0
    out = capsys.readouterr().out
    assert out.count("skipped") == 2
    assert len(calls(log)) == before
    sweeps = sorted((adapt / "_sweeps").glob("local-cc-agg1-*.json"))
    last = json.loads(sweeps[-1].read_text())
    statuses = [t["status"] for d in last["domains"] for t in d["tasks"]]
    assert statuses == ["skipped", "skipped"]


def test_tagged_run_is_found_by_every_read_back_command(tmp_path, fake_claude, monkeypatch, capsys):
    apply_fake_env(monkeypatch, reply=IMPROVER_REPLY, edit=f"CLAUDE.md={EDIT}\n")
    tasks_root = _tasks(tmp_path / "tasks")
    runs = tmp_path / "runs"
    common = ["--runs", str(runs), "--k", "1"]
    tag = ["--tag", "v2"]
    run = ["run", "--tasks", str(tasks_root), *common, "--only-domains", "writing", *tag]
    assert main(run) == 0
    assert (runs / "adapt" / "local-cc-agg1-v2" / "writing__notes" / "summary.json").is_file()
    assert "--tag v2" in capsys.readouterr().out  # the suggested `judge` line keeps the tag

    apply_fake_env(monkeypatch, reply=JUDGE_REPLY)
    assert main(["judge", "--tasks", str(tasks_root), *common]) == 1  # untagged: nothing there
    assert main(["judge", "--tasks", str(tasks_root), *common, *tag]) == 0
    assert (runs / "adapt" / "local-cc-agg1-v2" / "judged.json").is_file()
    assert main(["report", *common, *tag]) == 0
    assert main(["library", "show", *common, *tag]) == 0
    dest = tmp_path / "project"
    assert main(["export", *common, *tag, "--task", "writing/notes", "--dest", str(dest)]) == 0
    assert EDIT in (dest / "CLAUDE.md").read_text()
