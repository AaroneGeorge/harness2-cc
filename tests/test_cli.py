"""The `harness2` CLI end to end, in process, with no real `claude` and no network."""

from __future__ import annotations

import json
import os
import stat
import subprocess
import sys
from pathlib import Path

import pytest

from harness2 import config, tasks
from harness2.adapters.local import LocalTasksAdapter
from harness2.cli import main
from harness2.core import tree as HT
from harness2.substrate.surface import CLAUDE_CODE_SURFACE as S
from harness2.substrate.surface import unrender

ROOT = Path(__file__).resolve().parent.parent


def _logging_claude(tmp_path: Path, monkeypatch) -> Path:
    """A `claude` that only records that it was invoked; first on PATH and in config."""
    bindir = tmp_path / "bin"
    bindir.mkdir()
    log = tmp_path / "claude-invocations.log"
    exe = bindir / "claude"
    exe.write_text(
        f"#!{sys.executable}\nimport sys\n"
        f"open({str(log)!r}, 'a').write(' '.join(sys.argv[1:]) + '\\n')\n"
        "print('9.9.9 (Claude Code)')\n",
        encoding="utf-8",
    )
    exe.chmod(exe.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
    monkeypatch.setenv("PATH", f"{bindir}{os.pathsep}{os.environ.get('PATH', '')}")
    monkeypatch.setattr(config, "CLAUDE_BIN", str(exe))
    return log


def _tasks(tmp_path: Path) -> Path:
    root = tmp_path / "tasks"
    tasks.scaffold(root, "data-cleanup", "ledger", instruction="Sum the ledger.")
    tasks.scaffold(root, "data-cleanup", "contacts", instruction="Dedupe contacts.")
    tasks.scaffold(root, "writing", "notes", instruction="Write release notes.")
    return root


def test_help_runs_as_a_module():
    p = subprocess.run(
        [sys.executable, "-m", "harness2", "--help"], capture_output=True, text=True, cwd=ROOT
    )
    assert p.returncode == 0, p.stderr
    for command in ("run", "judge", "report", "demo", "check", "task", "export", "library"):
        assert command in p.stdout


def test_demo_writes_demo_json(tmp_path, capsys):
    out = tmp_path / "demo"
    assert main(["demo", "--output", str(out), "--k", "1", "--mode", "both"]) == 0
    got = json.loads((out / "demo.json").read_text())
    assert (got["done"], got["failed"]) == (2, 0)
    assert got["execution_count"] > 0 and got["editor_calls"] > 0
    assert "2 tasks completed" in capsys.readouterr().out


def test_task_new_and_list(tmp_path, capsys):
    root = tmp_path / "tasks"
    assert (
        main(
            [
                "task",
                "new",
                "writing",
                "notes",
                "--tasks",
                str(root),
                "--instruction",
                "Write notes.",
            ]
        )
        == 0
    )
    assert (root / "writing/notes/task.json").is_file()
    assert (root / "writing/notes/instruction.md").read_text() == "Write notes.\n"
    capsys.readouterr()
    assert main(["task", "list", "--tasks", str(root)]) == 0
    out = capsys.readouterr().out
    assert "writing/notes" in out and "output/answer.md" in out
    assert main(["task", "new", "writing", "notes", "--tasks", str(root)]) == 1
    assert "already exists" in capsys.readouterr().err


def test_task_split(tmp_path, capsys):
    root = _tasks(tmp_path)
    out = tmp_path / "split.json"
    assert main(["task", "split", "--seed", "3", "--output", str(out), "--tasks", str(root)]) == 0
    data = json.loads(out.read_text())
    assert sorted(data["train"] + data["test"]) == sorted(tasks.discover(root))


def test_run_dry_run_prints_stream_without_calling_claude(tmp_path, monkeypatch, capsys):
    log = _logging_claude(tmp_path, monkeypatch)
    root = _tasks(tmp_path)
    runs = tmp_path / "runs"
    argv = ["run", "--tasks", str(root), "--runs", str(runs), "--k", "1", "--dry-run"]
    assert main(argv) == 0
    out = capsys.readouterr().out
    for task_id in ("data-cleanup/contacts", "data-cleanup/ledger", "writing/notes"):
        assert task_id in out
    assert "local-cc-agg1" in out
    assert str(runs / "library" / "local" / "k1-cc") in out
    # data-cleanup has more tasks, so it comes first in the reference order
    assert out.index("data-cleanup/contacts") < out.index("writing/notes")
    assert not log.exists(), "dry run invoked claude"
    assert not runs.exists(), "dry run wrote to the runs directory"

    assert main(argv + ["--max-tasks", "1", "--mode", "both"]) == 0
    out = capsys.readouterr().out
    assert "data-cleanup/contacts" in out and "data-cleanup/ledger" not in out
    assert "local-cc-seq1" in out
    assert not log.exists()


def test_run_rejects_bad_mode_and_unknown_domain(tmp_path, monkeypatch, capsys):
    _logging_claude(tmp_path, monkeypatch)
    root = _tasks(tmp_path)
    base = ["run", "--tasks", str(root), "--runs", str(tmp_path / "runs"), "--k", "1"]
    assert main(base + ["--mode", "sideways", "--dry-run"]) == 1
    assert "error:" in capsys.readouterr().err
    assert main(base + ["--only-domains", "nope", "--dry-run"]) == 1
    assert "nope" in capsys.readouterr().err


def test_check_with_fake_claude(tmp_path, fake_claude, capsys):
    root = _tasks(tmp_path)
    assert main(["check", "--tasks", str(root)]) == 0, capsys.readouterr()
    out = capsys.readouterr().out
    assert "tasks: 3" in out and "Ready" in out


def test_check_without_tasks_is_a_note_not_a_failure(tmp_path, fake_claude, capsys):
    # The Quick start runs `harness2 check` before `harness2 task new`.
    assert main(["check", "--tasks", str(tmp_path / "none")]) == 0
    out = capsys.readouterr().out
    assert "FAIL" not in out and "tasks: none yet" in out and "harness2 task new" in out


def test_check_fails_on_an_invalid_task(tmp_path, fake_claude, capsys):
    root = _tasks(tmp_path)
    (root / "writing" / "notes" / "task.json").write_text('{"timeout_s": 0}')
    assert main(["check", "--tasks", str(root)]) == 1
    assert "FAIL: task writing/notes" in capsys.readouterr().out


def test_export_from_demo_run(tmp_path, capsys):
    out = tmp_path / "demo"
    assert main(["demo", "--output", str(out), "--k", "1", "--mode", "both"]) == 0
    project = tmp_path / "project"
    argv = [
        "export",
        "--runs",
        str(out / "runs"),
        "--task",
        "ledger-1",
        "--k",
        "1",
        "--dest",
        str(project),
    ]
    assert main(argv) == 0
    printed = capsys.readouterr().out
    claude_md = (project / "CLAUDE.md").read_text()
    assert "<!-- harness2 -->" in claude_md
    assert "python scripts/summarize.py" in claude_md
    script = (project / "scripts/summarize.py").read_text()
    assert "if amount > 0" not in script  # the adopted harness carries the fix
    assert str(project / "scripts/summarize.py") in printed

    assert main(argv) == 1
    assert "refusing" in capsys.readouterr().err
    assert main(argv + ["--force"]) == 0
    assert (project / "CLAUDE.md").read_text().count("<!-- harness2 -->") == 1

    assert main(argv[:-2] + ["--dest", str(tmp_path / "p2"), "--mode", "sequential"]) == 0


def test_export_appends_to_an_existing_claude_md(tmp_path, capsys):
    out = tmp_path / "demo"
    assert main(["demo", "--output", str(out), "--k", "1"]) == 0
    project = tmp_path / "project"
    project.mkdir()
    (project / "CLAUDE.md").write_text("# My project\n\nHouse rules.\n")
    assert (
        main(
            [
                "export",
                "--runs",
                str(out / "runs"),
                "--task",
                "ledger-1",
                "--k",
                "1",
                "--dest",
                str(project),
            ]
        )
        == 0
    )
    text = (project / "CLAUDE.md").read_text()
    assert text.startswith("# My project\n\nHouse rules.\n")
    assert text.index("<!-- harness2 -->") > text.index("House rules.")


def test_export_missing_run(tmp_path, capsys):
    assert (
        main(
            [
                "export",
                "--runs",
                str(tmp_path),
                "--task",
                "a/b",
                "--k",
                "1",
                "--dest",
                str(tmp_path / "p"),
            ]
        )
        == 1
    )
    assert "no summary" in capsys.readouterr().err


def _harness_tree() -> dict[str, str]:
    return {
        "CLAUDE.md": "You reconcile ledgers.\n",
        "guardrails.md": "- When reconciling, use the `recon` skill.\n",
        "MEMORY.md": "Ledgers are signed.\n",
        "skills/recon/SKILL.md": "---\ndescription: reconciling a ledger\n---\nSteps.\n",
        "agents/checker.md": "---\nname: checker\ndescription: checks totals\n---\nCheck.\n",
        "scripts/total.py": "print(1)\n",
    }


def test_render_round_trip(tmp_path, capsys):
    harness = HT.write_tree(S, _harness_tree(), tmp_path / "h")
    ws = tmp_path / "ws"
    assert main(["render", str(harness), str(ws)]) == 0
    assert (ws / ".claude/skills/recon/SKILL.md").is_file()
    assert (ws / ".claude/agents/checker.md").is_file()
    assert (ws / "scripts/total.py").is_file()
    back = {rel: text for rel, text in unrender(ws).items() if text.strip()}
    assert back == _harness_tree()
    assert main(["render", str(tmp_path / "nothing"), str(ws)]) == 1


def test_task_import_produces_a_valid_tree(tmp_path, capsys):
    project = tmp_path / "project"
    (project / ".claude/agents").mkdir(parents=True)
    (project / "CLAUDE.md").write_text("Always run the tests before finishing.\n")
    (project / ".claude/agents/x.md").write_text("---\ndescription: reviews diffs\n---\nReview.\n")
    dest = tmp_path / "imported"
    assert main(["task", "import", "--from", str(project), "--dest", str(dest)]) == 0
    got = HT.read_tree(S, dest)
    assert got["CLAUDE.md"] == "Always run the tests before finishing.\n"
    assert "agents/x.md" in got
    HT.validate(S, got)
    assert main(["task", "import", "--from", str(project), "--dest", str(dest)]) == 1


# ---- the adapter, over a stub substrate ------------------------------------------------------


class StubSubstrate:
    name = "cc"

    def __init__(self):
        self.calls = []

    def surface(self):
        return S

    def execute(self, task, harness_dir, run_dir, *, base_harness, timeout=None):
        self.calls.append((task.id, harness_dir, run_dir, base_harness, timeout))
        out = run_dir / "deliverables" / task.deliverables[0]
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text("Total: 85.15\n")
        return run_dir

    def delivered(self, run_dir, deliverables):
        return all((run_dir / "deliverables" / d).is_file() for d in deliverables)

    def trajectory(self, run_dir, *, obs_trunc, max_chars=400_000):
        return "[RESULT] subtype=success"

    def deliverable_files(self, run_dir, deliverables):
        return {d: (run_dir / "deliverables" / d).read_text() for d in deliverables}

    def deliverable_text(self, run_dir, deliverables):
        files = self.deliverable_files(run_dir, deliverables)
        return "\n\n".join(f"# {k}\n\n{v}" for k, v in files.items())


def _adapter(tmp_path) -> LocalTasksAdapter:
    return LocalTasksAdapter(
        _tasks(tmp_path),
        substrate=StubSubstrate(),
        base_harness=config.base_harness_dir(),
        runs_root=tmp_path / "runs",
    )


def test_adapter_maps_tasks_and_rollouts(tmp_path):
    a = _adapter(tmp_path)
    assert a.spec.name == "local" and a.spec.install_mode == "workspace_config"
    assert a.spec.allow_subagents and a.spec.leak_globs == tasks.LEAK_GLOBS
    assert a.tasks() == ["data-cleanup/contacts", "data-cleanup/ledger", "writing/notes"]
    assert a.reference_domain_order() == ["data-cleanup", "writing"]
    assert a.domain_of("writing/notes") == "writing"
    assert a.task_prompt("data-cleanup/ledger") == "Sum the ledger."
    assert a.task_files("data-cleanup/ledger") == a.tasks_root / "data-cleanup/ledger/workspace"
    rid = "local-cc-agg1/data-cleanup/ledger/base1"
    rd = a.execute("data-cleanup/ledger", None, rid)
    assert rd == tmp_path / "runs" / "rollouts" / rid
    assert a.substrate.calls[0][4] == tasks.DEFAULT_TIMEOUT_S
    assert json.loads((rd / "adapter.json").read_text()) == {"task_id": "data-cleanup/ledger"}
    assert a.delivered(rd)
    assert "Total: 85.15" in a.deliverable_text(rd)
    (rd / "adapter.json").unlink()
    assert a.task_of(rd) == "data-cleanup/ledger"
    assert a.validate_tree({}) is None


def test_adapter_prompts_overlay(tmp_path):
    a = _adapter(tmp_path)
    assert a.prompts_for("writing/notes") == a.spec.prompts
    (a.tasks_root / "writing/notes/prompts.json").write_text(json.dumps({"domain_word": "genre"}))
    got = a.prompts_for("writing/notes")
    assert got.domain_word == "genre" and got.work == a.spec.prompts.work
    (a.tasks_root / "writing/notes/prompts.json").write_text(json.dumps({"nope": "x"}))
    with pytest.raises(ValueError):
        a.prompts_for("writing/notes")


def test_adapter_score_check_then_rubric(tmp_path, monkeypatch):
    a = _adapter(tmp_path)
    rid = "local-cc-agg1/data-cleanup/ledger/agg1"
    rd = a.execute("data-cleanup/ledger", None, rid)
    assert a.score(rd, "data-cleanup/ledger") is None

    seen = {}

    def fake_rubric(text, instruction, criteria, *, model, claude_bin):
        seen.update(text=text, instruction=instruction, criteria=criteria, model=model)
        return {"n_passed": 1, "n_criteria": 1, "all_pass": True}

    monkeypatch.setattr("harness2.adapters.local.judge.rubric_judge", fake_rubric)
    task_dir = a.tasks_root / "data-cleanup/ledger"
    (task_dir / "rubric.json").write_text(json.dumps({"criteria": ["has a total"]}))
    assert a.score(rd, "data-cleanup/ledger")["n_passed"] == 1
    assert seen["criteria"] == ["has a total"] and "Total: 85.15" in seen["text"]
    assert seen["model"] == config.JUDGE_MODEL

    (task_dir / "check.py").write_text(
        "import json, pathlib, sys\n"
        "text = (pathlib.Path(sys.argv[1]) / 'output/answer.md').read_text()\n"
        "print(json.dumps({'n_passed': int('85.15' in text) + int('99' in text), "
        "'n_criteria': 2}))\n"
    )
    got = a.score(rd, "data-cleanup/ledger")
    assert (got["n_passed"], got["n_criteria"], got["all_pass"], got["metric"]) == (
        1,
        2,
        False,
        "check",
    )


def test_judge_and_report_cli(tmp_path, monkeypatch, capsys):
    log = _logging_claude(tmp_path, monkeypatch)
    a = _adapter(tmp_path)
    task_dir = a.tasks_root / "data-cleanup/ledger"
    (task_dir / "check.py").write_text(
        "import json\nprint(json.dumps({'n_passed': 2, 'n_criteria': 2}))\n"
    )
    runs = tmp_path / "runs"
    rid = "local-cc-agg1/data-cleanup/ledger/agg1"
    rd = a.execute("data-cleanup/ledger", None, rid)
    ns_dir = runs / "adapt" / "local-cc-agg1" / "data-cleanup__ledger"
    ns_dir.mkdir(parents=True)
    sel = {
        "task_id": "data-cleanup/ledger",
        "mode": "agg",
        "k": 1,
        "ok": True,
        "chosen_label": "agg1",
        "chosen_run_id": rid,
        "chosen_run_dir": str(rd),
        "chosen_is_base": False,
        "usd": 0.5,
    }
    summary = {
        "task_id": "data-cleanup/ledger",
        "domain": "data-cleanup",
        "k": 1,
        "status": "ok",
        "base": [{"label": "base1", "run_id": rid, "run_dir": str(rd)}],
        "modes": {"agg": {"ok": True, "stages": [{"usd": 1.25}]}},
        "selections": {"agg": sel},
        "errors": [],
    }
    (ns_dir / "summary.json").write_text(json.dumps(summary))
    common = ["--tasks", str(a.tasks_root), "--runs", str(runs), "--k", "1"]
    assert main(["judge", *common]) == 0
    judged = json.loads((ns_dir.parent / "judged.json").read_text())
    assert judged[0]["n_passed"] == 2 and judged[0]["graded"]
    assert main(["judge", *common, "--bases"]) == 0
    assert (ns_dir.parent / "base_judged.json").is_file()
    capsys.readouterr()
    assert main(["report", "--runs", str(runs), "--k", "1"]) == 0
    out = capsys.readouterr().out
    assert "score 100.00%" in out and "base k=1" in out
    assert "data-cleanup/ledger" in out and "agg1" in out and "1.75" in out
    assert not log.exists()
    assert main(["report", "--runs", str(runs), "--k", "2"]) == 1
