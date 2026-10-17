"""Task discovery, loading, scaffolding, split files and leak globs."""

from __future__ import annotations

import json

import pytest

from harness2 import tasks as T


def make(root, tid, meta=None, instruction_md=None):
    d = root / tid
    d.mkdir(parents=True)
    (d / "task.json").write_text(json.dumps(meta if meta is not None else {"instruction": "Do."}))
    if instruction_md is not None:
        (d / "instruction.md").write_text(instruction_md)
    return d


def test_discover_sorted_and_hidden_skipped(tmp_path):
    make(tmp_path, "writing/notes")
    make(tmp_path, "data/b")
    make(tmp_path, "data/a")
    make(tmp_path, ".hidden/x")
    make(tmp_path, "data/_draft")
    (tmp_path / "data" / "no_task_json").mkdir()
    found = T.discover(tmp_path)
    assert list(found) == ["data/a", "data/b", "writing/notes"]
    assert found["writing/notes"] == "writing"


def test_discover_empty_points_at_task_new(tmp_path):
    with pytest.raises(FileNotFoundError, match="harness2 task new"):
        T.discover(tmp_path)
    with pytest.raises(FileNotFoundError):
        T.discover(tmp_path / "missing")


def test_load_defaults(tmp_path):
    make(tmp_path, "d/t", {"instruction": "Do the thing.\n\n"})
    t = T.load(tmp_path, "d/t")
    assert t.id == "d/t" and t.domain == "d" and t.dir == tmp_path / "d" / "t"
    assert t.instruction == "Do the thing."
    assert t.deliverables == ["output/answer.md"]
    assert (t.timeout_s, t.max_budget_usd, t.effort, t.workspace) == (1800, 10.0, "", None)


def test_load_full_and_instruction_md(tmp_path):
    d = make(
        tmp_path,
        "d/t",
        {
            "deliverables": ["./output/r.md", "output/x.csv"],
            "timeout_s": 60,
            "max_budget_usd": 2,
            "effort": "high",
        },
        instruction_md="Long prompt.\n\n",
    )
    (d / "workspace").mkdir()
    t = T.load(tmp_path, "d/t")
    assert t.instruction == "Long prompt."
    assert t.deliverables == ["output/r.md", "output/x.csv"]
    assert (t.timeout_s, t.max_budget_usd, t.effort) == (60, 2.0, "high")
    assert t.workspace == d / "workspace"


def test_zero_budget_means_no_cap(tmp_path):
    make(tmp_path, "d/t", {"instruction": "x", "max_budget_usd": 0})
    assert T.load(tmp_path, "d/t").max_budget_usd == 0.0


def test_task_json_instruction_wins(tmp_path):
    make(tmp_path, "d/t", {"instruction": "From json."}, instruction_md="From md.")
    assert T.load(tmp_path, "d/t").instruction == "From json."


@pytest.mark.parametrize(
    "meta, needle",
    [
        ({}, "needs an `instruction`"),
        ({"instruction": "x", "deliverables": ["../escape.md"]}, "without '..'"),
        ({"instruction": "x", "deliverables": ["/abs.md"]}, "relative file path"),
        ({"instruction": "x", "deliverables": []}, "non-empty list"),
        ({"instruction": "x", "deliverables": "output/a.md"}, "non-empty list"),
        ({"instruction": "x", "effort": "turbo"}, "`effort`"),
        ({"instruction": "x", "timeout_s": 0}, "`timeout_s`"),
        ({"instruction": "x", "timeout_s": -5}, "`timeout_s`"),
        ({"instruction": "x", "max_budget_usd": -1}, "`max_budget_usd`"),
        ({"instruction": "x", "max_budget_usd": True}, "`max_budget_usd`"),
        ({"instruction": 3}, "`instruction` must be a string"),
        ([], "JSON object"),
    ],
)
def test_load_rejects(tmp_path, meta, needle):
    make(tmp_path, "d/t", meta)
    with pytest.raises(ValueError, match=needle.replace("(", r"\(")):
        T.load(tmp_path, "d/t")


def test_load_bad_json_and_ids(tmp_path):
    d = make(tmp_path, "d/t")
    (d / "task.json").write_text("{nope")
    with pytest.raises(ValueError, match="not valid JSON"):
        T.load(tmp_path, "d/t")
    with pytest.raises(FileNotFoundError):
        T.load(tmp_path, "d/missing")
    for bad in ("t", "a/b/c", "../t", "d/.."):
        with pytest.raises(ValueError):
            T.load(tmp_path, bad)


def test_scaffold(tmp_path):
    d = T.scaffold(tmp_path, "writing", "memo", instruction="Write a memo.")
    assert d == tmp_path / "writing" / "memo"
    assert (d / "workspace").is_dir() and not any((d / "workspace").iterdir())
    assert json.loads((d / "rubric.json.example").read_text())["criteria"]
    assert not (d / "rubric.json").exists()
    t = T.load(tmp_path, "writing/memo")
    assert t.instruction == "Write a memo." and t.workspace == d / "workspace"
    assert T.discover(tmp_path) == {"writing/memo": "writing"}
    with pytest.raises(FileExistsError):
        T.scaffold(tmp_path, "writing", "memo")
    with pytest.raises(ValueError):
        T.scaffold(tmp_path, "bad/domain", "x")
    placeholder = T.scaffold(tmp_path, "writing", "blank")
    assert T.load(tmp_path, "writing/blank").instruction
    assert (placeholder / "instruction.md").is_file()


def test_leak_globs():
    assert T.LEAK_GLOBS == ("rubric.json", "check.py", "judged.json", "*.judged.json")
    for rel in ("rubric.json", "d/t/check.py", "judged.json", "x/run.judged.json"):
        assert T.is_leak(rel), rel
    for rel in ("task.json", "instruction.md", "workspace/data.csv", "rubric.json.example"):
        assert not T.is_leak(rel), rel


TASKS = {"a/1": "a", "a/2": "a", "a/3": "a", "a/4": "a", "b/1": "b", "b/2": "b"}


def test_write_split_and_select(tmp_path):
    out = T.write_split(TASKS, 7, tmp_path / "split.json")
    data = json.loads(out.read_text())
    assert data["benchmark"] == "local" and data["seed"] == 7
    assert len(data["train"]) == 3 and len(data["test"]) == 3
    assert set(data["train"]) | set(data["test"]) == set(TASKS)
    assert T.select(TASKS, "train", out) == sorted(data["train"])
    assert T.select(TASKS, "test", out) == sorted(data["test"])
    assert T.select(TASKS, "all", out) == sorted(TASKS)
    again = T.write_split(TASKS, 7, tmp_path / "split2.json")
    assert json.loads(again.read_text()) == data
    with pytest.raises(FileExistsError):
        T.write_split(TASKS, 7, out)
    with pytest.raises(ValueError):
        T.write_split(TASKS, 7, tmp_path / "s3.json", train_frac=1.0)


def test_select_without_file():
    assert T.select(TASKS, "all", None) == sorted(TASKS)
    with pytest.raises(ValueError, match="requires --split-file"):
        T.select(TASKS, "train", None)
    with pytest.raises(ValueError, match="Unknown split"):
        T.select(TASKS, "dev", None)


@pytest.mark.parametrize(
    "data, needle",
    [
        ({"benchmark": "other", "train": [], "test": []}, "expected a split for local"),
        ({"benchmark": "local", "train": "a/1", "test": []}, "train must be a list"),
        ({"benchmark": "local", "train": ["a/1", "a/1"], "test": []}, "duplicate"),
        ({"benchmark": "local", "train": ["a/1"], "test": ["a/1"]}, "overlap"),
        ({"benchmark": "local", "train": ["z/9"], "test": []}, "absent from the dataset"),
    ],
)
def test_select_rejects_bad_split(tmp_path, data, needle):
    p = tmp_path / "s.json"
    p.write_text(json.dumps(data))
    with pytest.raises(ValueError, match=needle):
        T.select(TASKS, "train", p)
