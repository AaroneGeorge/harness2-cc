# Using Harness² for Claude Code

This guide follows one task through the whole loop, then covers writing tasks, reading the library, exporting a harness, and fixing common problems. The [README](../README.md) has install steps and a short quick start.

- [Command reference](#command-reference)
- [Walk-through: one task end to end](#walk-through-one-task-end-to-end)
- [Writing good tasks](#writing-good-tasks)
- [The library and exporting](#the-library-and-exporting)
- [Troubleshooting](#troubleshooting)

## Command reference

```
harness2 run     [--tasks DIR] [--runs DIR] --k K [--mode parallel|sequential|both]
                 [--workers N] [--only-domains a,b] [--only-tasks id,id] [--max-tasks N]
                 [--seed N] [--split all|train|test --split-file F] [--base-harness DIR]
                 [--tag T] [--dry-run]
harness2 judge   [--tasks DIR] [--runs DIR] --k K [--mode ...] [--seed N] [--tag T] [--bases]
                 [--parallel N]
harness2 report  [--runs DIR] --k K [--mode ...] [--seed N] [--tag T]
harness2 demo    [--output DIR] [--k K] [--mode ...]   # no model calls; defaults runs/demo, k=2, parallel
harness2 check   [--tasks DIR]                         # preflight: claude, config, tasks (none yet is fine)
harness2 task new <domain> <name> [--tasks DIR] [--instruction TEXT]
harness2 task list [--tasks DIR]
harness2 task split --seed N --output F [--train-frac 0.5] [--tasks DIR]
harness2 task import --from PROJECT_DIR --dest HARNESS_DIR      # unrender an existing project
harness2 export  [--runs DIR] --task ID --k K [--mode parallel] [--seed N] [--tag T] --dest PROJECT_DIR
                 [--force]
harness2 library show [--runs DIR] --k K [--mode parallel] [--domain D] [--seed N] [--tag T]
harness2 render  HARNESS_DIR WORKSPACE_DIR                       # install a tree into a workspace
```

`python -m harness2` is the same as `harness2`. `--runs` defaults to `HARNESS2_RUNS_DIR` (`runs`) and `--tasks` to `HARNESS2_TASKS_DIR` (`tasks`) everywhere. `--k`, `--seed` and `--tag` name a run, and `--mode` picks its modes: use the same values for `run`, `judge`, `report`, `library show` and `export`, or they will look in a different namespace. `run` exits with 0 when everything succeeded, 1 when a domain died or no task was scheduled, and 2 when some tasks failed.

**Demo output.** `harness2 demo` writes the same per-task layout under `<output>/runs/demo-cc-<mode><k>/<task>/` (default output `runs/demo`, no `adapt/` level, no domain prefix in the task names), its rollouts under `<output>/executions/` and its library under `<output>/library/k<k>/<mode>/<domain>/`. Of the read-back commands only `export` understands that layout, for example `harness2 export --runs runs/demo/runs --task ledger-1 --k 2 --dest /tmp/demo-project` (the demo prints this line). The demo tasks have no `check.py` or `rubric.json`, so there is nothing for `judge` or `report` to score, and `library show` does not read the demo's library: open `<output>/library/k<k>/agg/accounting/INDEX.md` directly.

## Walk-through: one task end to end

This follows `data-cleanup/ledger-reconcile` from [examples/tasks](../examples/tasks/) at `k=1` in parallel mode. Paths are relative to the runs root (`runs/` by default). The namespace is `local-cc-agg1`; the same task in sequential mode lives under `local-cc-seq1`. Inside a namespace a task's directory is its id with `/` replaced by `__`: `data-cleanup__ledger-reconcile`. Rollouts keep the `<domain>/<task>` form.

### 0. Preview

```bash
harness2 run --tasks examples/tasks --only-tasks data-cleanup/ledger-reconcile --k 1 --dry-run
```

This prints the task stream (domains, tasks in order) and the namespaces it would write, and exits. Nothing is written and no model is called. Check that the task you expect is listed.

### 1. Identity lock

```bash
harness2 run --tasks examples/tasks --only-tasks data-cleanup/ledger-reconcile --k 1
```

Before anything runs, `config.validate()`, validation of the base harness and a `claude --version` preflight must pass. Then `adapt/local-cc-agg1/config.json` records the run identity: method version, the executor, improver and judge models, the executor and improver effort levels, `--split`, the exact task stream, seed, `--max-tasks`, `--only-domains` and a fingerprint of the base harness. A later `run` with the same namespace but a different identity is refused (see [Troubleshooting](#identity-lock-mismatch)).

### 2. Base rollouts

The current harness (the empty v0, or `--base-harness`) is run `k` times. Each run gets its own directory:

```
rollouts/local-cc-agg1/data-cleanup/ledger-reconcile/base1/
  workspace/          copy of the task's workspace/ + the installed harness + an empty output/
  command.json        the exact claude command (and any flag the installed CLI did not accept)
  stream.jsonl        claude's stream-json output, one event per line
  stderr.txt
  result.json         total_cost_usd, num_turns, duration_ms, is_error, subtype
  trajectory.md       stream.jsonl rendered one event per line (see "Reading a trajectory")
  deliverables/       the declared deliverables, copied from workspace/ (here output/reconciliation.md)
  adapter.json        which task the run belongs to
  .adapt_complete     completion marker with the harness fingerprint
```

The base runs always live in the parallel namespace (`local-cc-agg1`), also when only sequential mode runs or both modes share them.

The executor runs as `claude -p` with `--permission-mode bypassPermissions`, `--setting-sources project`, `--no-session-persistence` and `--max-budget-usd` from `task.json` (left out when it is `0`), inside `workspace/`, with the instruction on stdin. A run that exceeds `timeout_s` is killed and recorded as an error; it never stops the sweep.

### 3. Propose

Two improver sessions run, `inv1` (how the agent works: procedure, checks, tools) and `dom1` (what a practitioner knows: domain facts and conventions). Each gets an evidence tree and edits a copy of the harness:

```
adapt/local-cc-agg1/data-cleanup__ledger-reconcile/
  harnesses/base/             the starting tree
  harnesses/inv1/             the tree after inv1's edits
  harnesses/dom1/
  context/inv1/               what the inv1 session could read
    task.md                   the task as the executor received it
    rollouts/base1.md         the base run's full trajectory
    deliverables/base1/       the base run's deliverables
    harness/                  the tree being edited (harvested back after the session)
    library/INDEX.md          earlier tasks in this domain, when there are any
  context/dom1/
```

The improver runs as `claude -p` with only `Read, Edit, Write, Glob, Grep` (no shell, no web), in a temporary directory that holds just this evidence tree. It is never pointed at the task directory, and the hidden files are never copied into the tree. Every session, in either mode, is also recorded under `adapt/_audit/local-cc-agg1/` as `<label>.a<attempt>.<cache key>.mission.md` (the instructions it got), `.response.md` (its reply), `.meta.json` (cost, attempts, model) and `.workspace/` (the harvested files); the reflection sessions are `experience.<mode><k>.<cache key>.*`.

An edited tree must pass validation (see "The harness surface" in the README). An invalid proposal is sent back once with the errors; if it still fails, or the session errors, the stage falls back to its input tree and is marked `fell_back` in the summary.

#### Reading a trajectory

Every rollout directory has a `trajectory.md`, and the files under `context/<label>/rollouts/` are the same rendering of `stream.jsonl`, one event per line:

```
[ASSISTANT] I'll start by reading both CSV files.
[ACTION] Read({"file_path": "bank.csv"})
[OBSERVATION] date,description,amount ...
[ACTION] Bash({"command": "python3 -c \"...\""})
[OBSERVATION ERROR] Traceback (most recent call last): ...
[ACTION] Write({"file_path": "output/reconciliation.md", "content": "# September ..."})
[OBSERVATION] File created successfully ...
[RESULT] subtype=success turns=14 cost_usd=0.41 duration_s=72.3
```

`[ACTION]` arguments are clipped to 400 characters. Long observations end with `…(+N chars — render truncation)`; that marker means the renderer cut it, not that the agent saw less. `[ERROR]` lines mark a failed run or an unparseable stream line. When you want to know why a harness change was made, read the trajectory moment the improver cited.

#### The `<notes>` block

Each improver replies with one `<notes>` block and edits the files directly. Proposers write, for each change, the trajectory moment that motivated it. Composers write one line per decision:

```
kept|revised|dropped -> <which edit> -> <the evidence, with counts where sets are involved>
```

An improver may also decide the evidence supports no change and keep the base tree (`KEEP_BASE`); the summary marks such a stage `kept_base`. Notes are stored in `summary.json` (per stage), in `adapt/_audit/.../<label>.a<attempt>.<cache key>.response.md`, and are passed to later stages as `context/<label>/decisions/<stage>.md`.

### 4. Probe

Each candidate is run once on the same task, exactly like a base run: `rollouts/local-cc-agg1/data-cleanup/ledger-reconcile/inv1/` and `.../dom1/`.

### 5. Compose

In parallel mode `agg1 .. aggk` each read the whole pool (base runs, every candidate harness and its rollout under `context/agg<i>/pool/<label>/`, and the earlier decisions) and write one composed harness to `harnesses/agg<i>/`. In sequential mode, round `t` proposes `inv_t` and `dom_t` against the previous checkpoint and composes `c_t` from that round's candidates plus every earlier round; its harnesses, contexts and rollouts are under `adapt/local-cc-seq1/...` and `rollouts/local-cc-seq1/...`. Each final is rolled once (`rollouts/.../agg1/`).

### 6. Selection

The selector chooses among the `k` finals. It sees each final's deliverables under an anonymous id (`A`, `B`, ...), shuffled with a seed derived from the task, `k` and mode. It never sees labels, harnesses, rubrics, checker output or scores.

At `k=1` there is only one final, so it is adopted without a selector session. The record says so:

```
adapt/local-cc-agg1/data-cleanup__ledger-reconcile/selection/agg1/selection.json
```

Fields in `selection.json`:

| field | meaning |
|---|---|
| `chosen_label`, `chosen_id`, `chosen_run_id`, `chosen_run_dir` | the adopted final, its anonymous id, and its rollout |
| `chosen_is_base` | whether a base run was chosen |
| `why` | the selector's reasoning (at `k=1`: "trivial pool: single final at k=1; delivered without a selector session") |
| `id_map` | anonymous id to label, so you can decode `why` |
| `n_candidates`, `dropped` | how many finals were compared, and which were left out and why (for example no deliverable) |
| `trivial`, `fallback`, `ok`, `error` | `trivial` at `k=1`; `fallback` when the selector failed and a neutral stand-in was taken |
| `evidence` | what the selector was shown (`deliverable`) |
| `usd` | selection and reflection spend |
| `experience` | the lesson the reflection session distilled (below) |

### 7. Reflection and the library

A reflection session compares the adopted harness with the base and the other candidates, reads the decisions and contrasts, and returns a lesson with `cue`, `lesson`, `evidence` and `confidence`. Its evidence tree is built under `adapt/local-cc-agg1/data-cleanup__ledger-reconcile/reflection/agg1/` (`adoption.json`, `harness/`, `base_harness/`, `pool/`, `decisions/`, `rollouts/`, `deliverables/`, and `edits.md` when the session described the edits), and the copy the session actually read is `reflection/agg1.experience/`. The adopted tree and the lesson go into the library:

```
library/local/k1-cc/agg/data-cleanup/
  INDEX.md
  harnesses/data-cleanup__ledger-reconcile/
    harness/          the full adopted tree
    EXPERIENCE.md
```

`EXPERIENCE.md` has the task id, the adopted label and run, an inventory of components, then up to three sections: **Why this was chosen** (the selection reasoning), **What each edit did**, and **Transferable lesson** (cue, lesson, evidence, confidence). A lesson without a cue, a lesson and evidence is not stored. `INDEX.md` lists every entry in the domain with its cue and paths. The next task in `data-cleanup` sees this as `context/<label>/library/` while it proposes.

### 8. Summary and resume

`adapt/local-cc-agg1/data-cleanup__ledger-reconcile/summary.json` holds the outcome for every mode that ran on the task (both modes write here, in the parallel namespace): the base runs, each mode's stages (label, dimension, notes, `fell_back`, `kept_base`, attempts, cost), the budget ledger (R and P), the selection per mode, errors and a status.

To resume, run the same command again. Three layers make this cheap:

- **Task:** a task whose `summary.json` is `ok` with an adopted output for every requested mode is skipped, after checking that the adopted run and harness are still on disk and unchanged. Its library entry is restored.
- **Rollout:** a run directory whose `.adapt_complete` marker matches the harness fingerprint is reused instead of re-run.
- **Improver session:** sessions are cached in `adapt/_audit/` by a fingerprint of their mission and evidence, so a finished session is not paid for twice.

Interrupting a sweep loses only the sessions and rollouts that were in flight. Each sweep also leaves a record, with every task's status (`ok`, `skipped`, `failed`), in `adapt/_sweeps/local-cc-agg1-<UTC stamp>.json`.

### 9. Compare base and adopted

```bash
harness2 judge --tasks examples/tasks --k 1
harness2 judge --tasks examples/tasks --k 1 --bases
harness2 report --k 1
```

`harness2 judge` scores the adopted run of every task with `check.py` (free) or `rubric.json` (one read-only judge session) and writes `adapt/local-cc-agg1/judged.json` (one file for both modes, in the parallel namespace). With `--bases` it scores the base runs instead, into `adapt/local-cc-agg1/base_judged.json`. `--parallel N` runs that many judge calls at once. Each `judged.json` row has `task_id`, `mode`, `k`, `label`, `run_id`, `chosen_is_base`, `n_passed`, `n_criteria`, `all_pass`, `raw` (the judge's full verdict), `error`, `frac` and `graded`. A `base_judged.json` row is shaped differently: `task`, `label`, `run_id` and `score`, where `score` is the judge's verdict (`n_passed`, `n_criteria`, `all_pass`, ...) or `null` when it failed.

`harness2 report` prints one line per mode and `k`: the mean score, which kind of output was picked (final or base), how many stages fell back, how many tasks are ungraded, and the spend; once `judge --bases` has run it adds a `base k=1` line for the starting harness. To compute the two means yourself:

```bash
python3 - <<'PY'
import json, statistics
ns = "runs/adapt/local-cc-agg1"
adopted = [r["frac"] for r in json.load(open(f"{ns}/judged.json")) if r["graded"]]
base = [s["n_passed"] / s["n_criteria"]
        for s in (r["score"] for r in json.load(open(f"{ns}/base_judged.json"))) if s]
for name, fracs in (("adopted", adopted), ("base", base)):
    mean = 100 * statistics.mean(fracs) if fracs else float("nan")
    print(f"{name}: {len(fracs)} graded, mean {mean:.1f}%")
PY
```

One task and one base run are anecdotes. Compare over several tasks, and at `k > 1` over several base runs, before drawing a conclusion.

## Writing good tasks

**Make the workspace self-contained.** Everything the executor needs goes in `workspace/`, which is copied fresh for every run. Do not rely on absolute paths, files in your home directory or network resources unless the task is about them. Keep the expected answer out of `workspace/`: `check.py` and `rubric.json` sit next to it in the task directory and are not copied.

**Put deliverables under `output/`.** Every run starts with an empty `output/` directory. List each deliverable in `task.json` and name the same paths in the instruction. Only declared deliverables are copied out of the workspace, and they are the only thing the selector reads, so work that does not land in a declared file is invisible to selection. Prefer text formats: a binary deliverable is shown to the selector as `(binary file, N bytes)` and cannot be compared.

**Be explicit.** Spell out formats, rules and edge cases (for example the matching rule in `ledger-reconcile`). An ambiguous instruction gives the improver noise to optimise and makes any checker arbitrary.

**Size timeouts and budgets.** `timeout_s` is a wall-clock limit per executor run and `max_budget_usd` a spend cap per executor run. Set them comfortably above what a normal run needs: a base run that times out leaves the improver little to learn from. They do not bound the improver sessions; `HARNESS2_IMPROVER_MAX_BUDGET_USD` does.

**Use domains on purpose.** The domain directory is the library key. Tasks in one domain run in order, and each reads the adopted harnesses and lessons of the earlier ones. Group tasks that share know-how; a domain of one task never benefits from the library, and a domain of unrelated tasks carries irrelevant lessons forward. `--workers N` runs different domains concurrently.

**Write hidden checks that cannot be gamed by wording.** A `check.py` should use only the standard library, find its own data relative to `__file__`, read deliverables from `<deliverables_dir>/<declared path>`, always print `{"n_passed": ..., "n_criteria": ...}` (with `n_passed` 0 when a file is missing) and exit 0. Rubric criteria should be checkable from the deliverables alone and name concrete values, as in the two example `rubric.json` files.

**Train and test splits.** To keep some tasks unseen by the library:

```bash
harness2 task split --seed 42 --output splits/local.json
harness2 run --split train --split-file splits/local.json --k 1
```

The split file is `{"benchmark": "local", "train": [...], "test": [...]}`, stratified by domain, with a default train fraction of 0.5 (`--train-frac`).

### Prompt overrides

The loop describes your task to the improver in task-neutral words. A task directory may carry a `prompts.json` that overrides any of these keys: `work` (one sentence on what the work is), `domain_word`, `deliverable_word`, `inv_levers`, `dom_levers`, `yardstick`, `domain_guidance`, `selection_checks`. Leave them out unless you have a reason. Do not mention benchmarks, evaluation, graders, rubrics or scores in them; that would leak judging into the loop.

## The library and exporting

```bash
harness2 library show --k 1 --mode parallel --domain data-cleanup
```

prints a domain's `INDEX.md` (from `runs/library/local/k1-cc/agg/data-cleanup/`), with one entry per adopted task. Each entry is a directory named after the task id with `/` replaced by `__` (`harnesses/data-cleanup__ledger-reconcile/`); its `EXPERIENCE.md` explains why that harness was adopted and what it is expected to transfer, and its `harness/` directory is the complete tree. Libraries are separate per runs root, `k`, mode (`agg` or `seq`), seed and tag (`library/local/k<k>-cc[-s<seed>][-<tag>]/<mode>/<domain>/`), so two experiments never share one. A task whose reflection produced no usable lesson adds no entry.

To see exactly what the executor saw for an adopted harness:

```bash
harness2 render runs/library/local/k1-cc/agg/data-cleanup/harnesses/data-cleanup__ledger-reconcile/harness /tmp/preview
```

To install it into a real project:

```bash
harness2 export --runs runs --task data-cleanup/ledger-reconcile --k 1 --mode parallel --dest ~/my-project
```

`export` copies the adopted harness of the chosen label:

- `CLAUDE.md` is created, or, if the project already has one, the harness sections are appended under a `<!-- harness2 -->` marker;
- `.claude/agents/`, `.claude/skills/` and `scripts/` are copied;
- `hooks.json` is merged into `.claude/settings.json`, keeping existing keys;
- an existing file is never overwritten unless you pass `--force`;
- every path written is printed.

Review the result before committing it. The harness was tuned on one task; check that its rules make sense for the rest of your project, and that any hook commands point at scripts that now exist in the project.

To go the other way, `harness2 task import --from ./my-project --dest ./my-harness` turns an existing project's `CLAUDE.md`, `.claude/` and `scripts/` into a harness tree that `harness2 run --base-harness ./my-harness` can start from.

## Troubleshooting

### `claude` not found

`harness2 check` and `harness2 run` stop if the `claude` binary is missing or `claude --version` fails. Install Claude Code, make sure `which claude` finds it in the same shell, or set `HARNESS2_CLAUDE_BIN` to its absolute path. If it is found but runs fail at once, check `stderr.txt` in the rollout: usually the login has expired. Run `claude` interactively and log in again, or export `ANTHROPIC_API_KEY`.

### Running harness2 from inside Claude Code

Claude Code sets `CLAUDECODE` and `CLAUDE_CODE_ENTRYPOINT` in its child processes, and `claude` refuses to start a nested session when it sees them. harness2 removes exactly these two variables from the environment of every `claude` it launches, so you can run it from a Claude Code session without doing anything. Nothing else in the environment is changed; in particular `ANTHROPIC_API_KEY` is passed through.

### Budget exceeded

Claude Code checks `--max-budget-usd` between turns, not during one, so a session can spend well past its cap before it is stopped: a session started with `--max-budget-usd 0.50` has been seen to finish at $1.60. A capped session ends with `is_error: true`, `subtype: "error_max_budget_usd"`, an `errors` list and no `result`.

- **Executor run.** It stops early and its `result.json` records `subtype: "error_max_budget_usd"` (the trajectory ends with `[ERROR] error_max_budget_usd: ...`). Whatever deliverables it had written are still collected. Raise `max_budget_usd` in `task.json`, set it to `0` to pass no cap at all, or make the task smaller.
- **Improver session.** A capped improver session is a **failed session**: its edits are discarded, not used. It is retried once (`HARNESS2_TRANSPORT_ATTEMPTS=2`), and if the retry is capped too the stage falls back to the harness it inherited; `harness2 report` counts it under "fell back", and the error in `summary.json` names the spend (`session spent $1.60 against the HARNESS2_IMPROVER_MAX_BUDGET_USD=... cap`). Both attempts are paid for. The default cap is $25 for this reason; raise it if this happens, or set `HARNESS2_IMPROVER_MAX_BUDGET_USD=0` to pass no cap.

### Timeouts

A run longer than `timeout_s` is killed with its process group and recorded as an error. Raise `timeout_s`, or check the trajectory for a loop the agent got stuck in.

### The judge returned None

A row in `judged.json` with `n_passed: null`, `graded: false` and an `error` means the judge failed (the checker crashed, printed something that is not the expected JSON, or the rubric judge did not return a parsable verdict). That is **not** a score of zero: `harness2 report` lists it as ungraded and leaves it out of the mean. Run `python3 check.py <run_dir>/deliverables` by hand to debug a checker, then rerun `harness2 judge`.

### Identity lock mismatch

`harness2 run` refuses to continue when `<runs>/adapt/local-cc-agg<k>[-s<seed>][-<tag>]/config.json` (one lock per `k`, seed and tag, shared by both modes) was written with a different identity: models, effort levels, `--split`, task stream, seed, `--max-tasks`, `--only-domains` or base harness (a fingerprint of its files, so editing the `--base-harness` directory counts too). The error names the keys that changed. This protects you from mixing two experiments in one namespace. Start a new one with a fresh runs root, for example:

```bash
export HARNESS2_RUNS_DIR=runs/opus-k1-v2
```

or pass `--runs`, or use `--tag` to get a different namespace in the same root.

### No tasks found

`harness2 run` and `harness2 check` look in `HARNESS2_TASKS_DIR` (default `tasks`) for `<domain>/<task-id>/task.json`. `harness2 run` stops when there are none; `harness2 check` only notes it (so it can run before your first `task new`) and still fails on a task that does not load. Pass `--tasks DIR`, or create a task with `harness2 task new <domain> <name>`. `harness2 task list` shows what was found.

### A deliverable is missing

If the selector or judge reports no deliverable, compare the paths in `task.json` with what the agent wrote: look in the rollout's `workspace/output/` and search `stream.jsonl` for `Write`. The instruction must name the same paths as `deliverables`.
