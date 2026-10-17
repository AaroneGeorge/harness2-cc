# Contributing

## Running the tests

```bash
uv venv .venv --python 3.12 && source .venv/bin/activate && uv pip install -e '.[dev]'
pytest
```

`harness2 demo` is a quick end-to-end smoke test of the loop and makes no model calls. If you have [ruff](https://docs.astral.sh/ruff/) installed, `ruff check .` uses the settings in `pyproject.toml`.

## Rules

- **No network and no real `claude` in tests.** Tests that need Claude Code put a fake `claude` script first on `PATH` that prints canned `stream-json` or `json` output (see `tests/test_substrate.py` and `tests/test_proposer.py`). A test must pass on a machine with no login and no internet.
- **Standard library only.** `dependencies` in `pyproject.toml` stays empty. `pytest` is the only development dependency.
- **Keep upstream headers.** Files that began as upstream harness2 code keep the Google LLC Apache-2.0 header unchanged and add one line below it:

  ```python
  # Modifications Copyright 2026 harness2-claude-code contributors.
  ```

  New files carry this project's own Apache-2.0 header. Prompts in `harness2/core/prompts.py` that the design marks as byte-for-byte ports are not edited.
- **`docs/DESIGN.md` is the contract.** A change to a CLI flag, an on-disk path or a public signature updates DESIGN.md, the README and `docs/USAGE.md` in the same change.
- **Never let judging reach the loop.** Nothing derived from `rubric.json`, `check.py` or `judged.json` may enter an improver, selector or reflector context.

## Adding a validation rule

Harness trees are validated in `harness2/core/tree.py` before every probe.

1. Add the check inside `validate()`. Append a message to the error list rather than raising, and make the message say how to fix the tree; the improver receives it verbatim on its retry.
2. If the rule depends on the surface, read it from the `SurfaceSpec` fields instead of hard-coding a path.
3. Add a test to `tests/test_tree.py` with one tree that breaks only the new rule and asserts the `ValueError` names it, and make sure the existing valid-tree tests still pass.
4. If the improver needs to know the rule to follow it, state it in the Claude Code surface block in `harness2/core/prompts.py`, and list it under "The harness surface" in the README.
