# Harness² for Claude Code

Harness² keeps the model fixed and improves the **harness**: the files around the model that shape how it works. In Claude Code that means `CLAUDE.md`, always-on guardrails, durable memory, tool guidance, skills, subagents, helper scripts and hooks. For each of your tasks, the loop runs Claude Code with the current harness, has an improver session edit those files, probes every edited harness on the same task, composes the edits the evidence supports, and adopts one result after an anonymised comparison of the deliverables. A per-domain library stores each adopted harness with the lesson behind it, so later tasks in the same domain start from what earlier ones learned. This is a Python port of Google Research's [harness2](https://github.com/google-research/harness2) ([paper](https://arxiv.org/abs/2610.14224)) to the Claude Code CLI.

## How it works

For one task at budget `k`:

```
task
 |
 |  base1 .. basek            roll the current harness k times              k rollouts
 v
PROPOSE  inv1 .. invk         INV: how the agent works                        k sessions
         dom1 .. domk         DOM: what a practitioner in the domain knows    k sessions
 |
 v
PROBE    run each candidate harness once on the same task                    2k rollouts
 |
 v
COMPOSE  parallel:   agg1 .. aggk   k independent compositions of the pool    k sessions
         sequential: c1 .. ck       c_t refines c_(t-1) with that round's      + k rollouts
                                    inv_t / dom_t and all earlier evidence      of the finals
 |
 v
SELECT   anonymised: the selector sees only the finals' deliverables,         1 session
         labelled A, B, C ...; no rubric, score or grader output              (k > 1 only)
 |
 v
REFLECT  adopted harness + an evidence-backed lesson                          1 session
 |
 v
LIBRARY  library/.../<mode>/<domain>/  read by the next task in the same domain before it proposes
```

Budget identity per task and mode: **R = 4k rollouts** (k base + 2k candidates + k finals) and **P = 3k improver sessions** (k INV + k DOM + k compositions). Selection and reflection are counted separately. The base rollouts are shared when both modes run.

The improver never sees a rubric, a checker or a score. It reads trajectories, deliverables, the harness being edited and the domain library, and it must tie each edit to something it saw in a trajectory.

## What is different from upstream

- **One executor: Claude Code.** Every role (executor, improver, selector, reflector, judge) is `claude -p`. Upstream's OpenCode and Codex executors are not included.
- **Your normal `claude` login.** OAuth (a Claude subscription) or `ANTHROPIC_API_KEY`, whatever `claude` already uses. No Vertex AI, no Google Cloud CLI.
- **No Docker and no benchmarks.** The JobBench, LAB and WorkBuddy-Bench integrations are removed. You bring your own tasks as small directories (see [Tasks](#tasks)).
- **The harness surface is Claude Code's.** Skills land in `.claude/skills/`, subagents in `.claude/agents/`, hooks in `.claude/settings.json`, and the four always-on files become sections of `CLAUDE.md`.
- **Judging is yours too.** A task can ship a deterministic `check.py` or a `rubric.json` that a Claude judge applies. Both are optional and hidden from the loop.
- **One CLI.** `harness2 <subcommand>` replaces upstream's separate entry points, and adds `task new`, `task import`, `export`, `render` and `library show`.
- **Platforms:** macOS and Linux. Python 3.11 or newer, standard library only.

The method itself (propose, probe, compose, anonymised selection, reflection, the per-domain library and the budget identity) is ported unchanged.

## Install

Requirements: Python 3.11 or newer, [uv](https://docs.astral.sh/uv/) (or plain `pip`), and the `claude` CLI on your `PATH` with a working login (`claude -p "hi"` should answer). There are no runtime dependencies.

```bash
uv venv .venv --python 3.12 && source .venv/bin/activate && uv pip install -e '.[dev]'
```

The `dev` extra only adds `pytest`.

## Quick start

Run these from the repository root, in order (step 4 copies a task from `examples/`; `tasks/` and `runs/` are created in the current directory).

1. Run the deterministic demo. It exercises the recursion (parallel mode, `k=2`, on two toy tasks; add `--mode both` for sequential too) with scripted editors and real Python executions, makes no model calls, and writes under `runs/demo/` (`--output` to change).

   ```bash
   harness2 demo
   ```

2. Preflight: checks that `claude` is on `PATH` and runs, that the configuration and the base harness are valid, and what tasks it can find (none yet is fine at this point).

   ```bash
   harness2 check
   ```

3. Create a task. This writes `tasks/data-cleanup/ledger-reconcile/` with `task.json`, `instruction.md`, an empty `workspace/` and a commented `rubric.json.example`.

   ```bash
   harness2 task new data-cleanup ledger-reconcile
   ```

4. Edit the task: write the prompt in `instruction.md`, list the files you expect in `task.json` under `deliverables`, and put the input files in `workspace/`. To use the shipped example instead of writing your own:

   ```bash
   cp -R examples/tasks/data-cleanup/ledger-reconcile/. tasks/data-cleanup/ledger-reconcile/
   ```

5. Preview the run. `--dry-run` prints the task stream and namespaces and touches no model.

   ```bash
   harness2 run --k 1 --max-tasks 1 --dry-run
   ```

6. Run it for real. At `k=1` in one mode this is 4 Claude Code executor runs and 4 improver-side sessions (see [Costs](#costs)).

   ```bash
   harness2 run --k 1 --max-tasks 1
   ```

7. Score the adopted run against the task's hidden `check.py` or `rubric.json`, then print the summary.

   ```bash
   harness2 judge --k 1
   harness2 report --k 1
   ```

8. Read what the library learned, and install the adopted harness into a project of yours.

   ```bash
   harness2 library show --k 1
   harness2 export --task data-cleanup/ledger-reconcile --k 1 --dest ~/my-project
   ```

`export` writes `CLAUDE.md` (or appends under a `<!-- harness2 -->` marker if one exists), copies `.claude/agents/`, `.claude/skills/` and `scripts/`, merges hooks into `.claude/settings.json` keeping existing keys, refuses to overwrite files unless you pass `--force`, and prints what it wrote.

Rerunning the same `harness2 run` command resumes. Main `run` flags:

| flag | meaning |
|---|---|
| `--tasks DIR` | tasks root (default `HARNESS2_TASKS_DIR`, `tasks`) |
| `--runs DIR` | output root (default `HARNESS2_RUNS_DIR`, `runs`) |
| `--k K` | recursion budget per task (required) |
| `--mode parallel\|sequential\|both` | recursion mode; `parallel` when omitted |
| `--workers N` | domains run concurrently; tasks inside a domain always run in order |
| `--only-domains a,b` / `--only-tasks id,id` | restrict the task stream |
| `--max-tasks N` | cap on tasks per domain |
| `--seed N` | reproducible shuffle of the task order; becomes part of the namespace |
| `--split all\|train\|test --split-file F` | use a split written by `harness2 task split` |
| `--base-harness DIR` | start from this harness tree instead of the empty v0 |
| `--tag T` | suffix for the namespace, to keep experiments apart; pass the same `--tag` to `judge`, `report`, `library show` and `export` |
| `--dry-run` | print the plan and exit without model calls |

Every subcommand takes `--help`. The full command reference is in [docs/USAGE.md](docs/USAGE.md#command-reference).

## Tasks

A tasks directory is `<tasks>/<domain>/<task-id>/`. The task id is `<domain>/<task-id>`, and the domain (the parent directory name) is the key for the experience library.

```
tasks/
  data-cleanup/
    ledger-reconcile/
      task.json        required
      instruction.md   optional: the task prompt, verbatim (preferred for long prompts)
      workspace/       optional: copied into the executor's working directory before each run
      rubric.json      optional, hidden: {"criteria": ["...", "..."]}
      check.py         optional, hidden: deterministic checker, takes precedence over rubric.json
```

A full `task.json`:

```json
{
  "instruction": "Reconcile bank.csv against ledger.csv and write output/reconciliation.md.",
  "deliverables": ["output/reconciliation.md"],
  "timeout_s": 900,
  "max_budget_usd": 5,
  "effort": "high"
}
```

| field | default | meaning |
|---|---|---|
| `instruction` | from `instruction.md` | the prompt, passed to `claude -p` on stdin; may be omitted when `instruction.md` exists |
| `deliverables` | `["output/answer.md"]` | paths, relative to the workspace, that the run must produce; only these are shown to the selector |
| `timeout_s` | `1800` | wall-clock limit per executor run; a run that hits it is killed and recorded as an error |
| `max_budget_usd` | `10.0` | passed to each executor run as `--max-budget-usd`; `0` passes no cap |
| `effort` | unset | executor `--effort`; unset leaves the Claude Code default |

The two hidden files are for scoring after the loop has finished, and only `harness2 judge` reads them:

- **`check.py`** is run as `python3 check.py <deliverables_dir>`, where `<deliverables_dir>` holds the run's deliverables at their declared paths (so it reads `<deliverables_dir>/output/reconciliation.md`). It prints `{"n_passed": int, "n_criteria": int}`. It costs nothing to run.
- **`rubric.json`** lists criteria. A Claude judge session (read-only) reads the deliverables and the instruction and returns pass or fail per criterion. This costs one model session per judged run.

`rubric.json`, `check.py`, `judged.json` and `*.judged.json` are never copied into any improver, selector or reflector context; the evidence builder refuses to proceed if one leaks. A task directory may also carry a `prompts.json` that overrides the loop's task-neutral wording (see [docs/USAGE.md](docs/USAGE.md#prompt-overrides)).

[examples/tasks/](examples/tasks/) has three ready-made tasks in two domains.

## The harness surface

A harness is a directory tree. The shipped base harness (v0) is empty: four always-on files that hold only an HTML comment, plus three README stubs. That is the same starting point as upstream's "bare stock run". When a harness is installed into the executor's workspace, each piece lands here:

| harness path | component | installed into the executor workspace as |
|---|---|---|
| `CLAUDE.md` | c1 role and procedure | first section of `<ws>/CLAUDE.md` |
| `guardrails.md` | c6 always-on rules | `## Guardrails` section of `<ws>/CLAUDE.md` |
| `MEMORY.md` | c3 durable domain facts | `## Memory` section of `<ws>/CLAUDE.md` |
| `tool_guidance.md` | c4 tool prose | `## Tool guidance` section of `<ws>/CLAUDE.md` |
| `skills/<name>/SKILL.md` (+ `skills/<name>/scripts/*`) | c6 on-demand skill | `<ws>/.claude/skills/<name>/...`; a skill helper therefore runs as `python3 .claude/skills/<name>/scripts/<file>.py` |
| `agents/<name>.md` | c5 subagent | `<ws>/.claude/agents/<name>.md` |
| `scripts/<name>.py` | c7 deterministic helper | `<ws>/scripts/<name>.py` |
| `hooks.json` | c8 hooks (tool-loop lever) | merged as `"hooks"` into `<ws>/.claude/settings.json` |
| (c2 context policy) | not injectable | Claude Code owns context assembly |

Empty always-on files are skipped, and if all four are empty no `CLAUDE.md` is written. Every edited tree is validated before it is probed:

- the four always-on files together stay under 24,000 characters (every one of them is in context on every turn);
- skills need a `description:` in their frontmatter, an optional `name:` must equal the directory, and every skill name must appear in `CLAUDE.md` or `guardrails.md` (a trigger line is added automatically when missing);
- subagents need `description:`, and an optional `name:` must equal the file stem;
- `scripts/*.py` are snake_case and must compile, as must skill scripts; any script an always-on file or skill invokes must exist in the tree (`python3 .claude/skills/<name>/scripts/<file>.py`, the path a skill helper has at runtime, resolves to `skills/<name>/scripts/<file>.py`);
- `hooks.json` must be valid JSON in Claude Code's hook schema with only `"type": "command"` entries, on these events: `PreToolUse`, `PostToolUse`, `UserPromptSubmit`, `Stop`, `SubagentStart`, `SubagentStop`, `SessionStart`, `SessionEnd`, `PreCompact`, `Notification`, `PermissionRequest`.

`harness2 render HARNESS_DIR WORKSPACE_DIR` installs a tree into a directory by hand so you can inspect exactly what the executor sees.

## Configuration

All settings are optional environment variables. They are read from the shell, then from `.env` in the current directory; the shell wins.

| variable | default | meaning |
|---|---|---|
| `HARNESS2_EXECUTOR_MODEL` | `claude-opus-5-5` | executor `--model` |
| `HARNESS2_EXECUTOR_EFFORT` | empty | executor `--effort`; empty leaves the CLI default |
| `HARNESS2_IMPROVER_MODEL` | `claude-opus-5-5` | improver, selector and reflector model |
| `HARNESS2_IMPROVER_EFFORT` | `high` | improver `--effort` |
| `HARNESS2_IMPROVER_MAX_BUDGET_USD` | `25` | `--max-budget-usd` per improver session; `0` passes no cap |
| `HARNESS2_JUDGE_MODEL` | `claude-opus-5-5` | rubric judge model |
| `HARNESS2_RUNS_DIR` | `runs` | output root |
| `HARNESS2_TASKS_DIR` | `tasks` | tasks root |
| `HARNESS2_CLAUDE_BIN` | `claude` | CLI binary |
| `HARNESS2_PHASE_WIDTH` | `2` | concurrent executor runs per phase |
| `HARNESS2_PROPOSER_CONCURRENCY` | `2` | concurrent improver sessions |
| `HARNESS2_TRANSPORT_ATTEMPTS` | `2` | attempts per improver call |

Model ids are bare Anthropic ids such as `claude-opus-5-5`. A bad effort level, a non-positive width, a negative budget or a missing `claude` binary stops the run with a one-line fix.

## Costs

Every task makes real model calls. Per task, per mode:

| | executor runs | improver-side sessions |
|---|---|---|
| `--k 1` | 4 | 3 + 1 reflection = 4 (no selector at k=1) |
| `--k 2` | 8 | 6 + 1 selector + 1 reflection = 8 |
| `--k 1 --mode both` | 7 (the k base runs are shared) | 6 + 2 reflections = 8 |
| `--k 2 --mode both` | 14 | 12 + 2 selectors + 2 reflections = 16 |

In general: 4k executor runs and 3k improver sessions per mode, plus 1 selector session when k > 1 and 1 reflection session. Improver retries (`HARNESS2_TRANSPORT_ATTEMPTS`, and one retry of a proposal that fails validation) can add sessions. `harness2 judge` adds one session per judged run for `rubric.json` tasks and nothing for `check.py` tasks; `--bases` also judges the k base runs.

Each executor run is given the task's `max_budget_usd` and each improver-side session `HARNESS2_IMPROVER_MAX_BUDGET_USD` as `--max-budget-usd` (`0` passes no cap). These are soft caps, not estimates and not hard ceilings: Claude Code checks the cap between turns, so a session can overshoot it (a `--max-budget-usd 0.50` session has been seen to finish at $1.60), and retries add sessions. A capped improver session is a failed session: its edits are discarded, it is retried once, and if it fails again the stage falls back to the harness it inherited, so a tight improver cap wastes money rather than saving it. That is why the improver default is $25. `result.json` in every rollout and the `*.meta.json` audit files record what each session actually cost, and `harness2 report` prints the spend per mode.

- **Start small:** `harness2 run --k 1 --max-tasks 1 --dry-run`, then the same command without `--dry-run`.
- **Subscription or API key:** with a Claude subscription (OAuth login) the runs count against your plan's usage limits; with `ANTHROPIC_API_KEY` they are billed to that key. harness2 uses whatever `claude` is logged in with.
- **What loads in each executor run:** the executor passes `--setting-sources project`, so your user-level settings and hooks are **not** applied; only the harness's `.claude/settings.json` is. Your global `~/.claude/CLAUDE.md` is still loaded by Claude Code's memory discovery, so it becomes part of every run. Keep it short, or be aware it is a fixed part of the harness being optimised.
- **`--bare` is not used** because it disables OAuth and keychain authentication, which would break subscription logins.

## Outputs

Everything goes under the runs root (`runs/` by default). Namespaces are `local-cc-<mode><k>[-s<seed>][-<tag>]`, with `agg` for parallel and `seq` for sequential, for example `local-cc-agg1`. Inside a namespace a task directory is the task id with `/` replaced by `__`.

```
runs/
  adapt/local-cc-agg1/<domain>__<task>/      summary.json (both modes), harnesses/<label>/, context/<label>/,
                                             selection/agg1/selection.json, reflection/agg1/ and
                                             reflection/agg1.experience/ (the reflection's evidence)
  adapt/local-cc-seq1/<domain>__<task>/      the same for sequential mode, minus summary.json
  adapt/local-cc-agg1/config.json            identity lock (models, efforts, split, task stream, seed,
                                             max_tasks, domains, base-harness fingerprint)
  adapt/local-cc-agg1/judged.json            written by harness2 judge; base_judged.json by judge --bases
  adapt/_audit/local-cc-agg1/                every improver-side session of both modes: *.mission.md,
                                             *.response.md, *.meta.json, *.workspace/
  adapt/_sweeps/local-cc-agg1-<stamp>.json   one record per sweep, with each task's status
  rollouts/<ns>/<domain>/<task>/<label>/     one executor run: workspace/, command.json, stream.jsonl,
                                             trajectory.md, result.json, deliverables/, .adapt_complete
  library/local/k1-cc/<mode>/<domain>/       INDEX.md, harnesses/<domain>__<task>/{harness/,EXPERIENCE.md}
```

The base runs are shared by both modes and always live in the parallel namespace (`rollouts/local-cc-agg1/.../base1/`). `trajectory.md` in each rollout is the run rendered one event per line, the same text the improver reads. `harness2 demo` uses a different, self-contained layout under its `--output` directory; see [docs/USAGE.md](docs/USAGE.md#command-reference).

[docs/USAGE.md](docs/USAGE.md) walks through each of these for one task.

## Using an existing project's harness as the starting point

If you already have a Claude Code project with a `CLAUDE.md`, skills, subagents, scripts or hooks, start the loop from that instead of the empty v0:

```bash
harness2 task import --from ./my-project --dest ./my-harness
harness2 run --base-harness ./my-harness --k 1 --max-tasks 1
```

`task import` is the inverse of the install step: `CLAUDE.md` is read whole into the `CLAUDE.md` slot unless it has the exact `## Guardrails`, `## Memory` and `## Tool guidance` headers that harness2 itself writes, in which case those sections are split back out. Only harness files are imported, not your project's code; the task's own `workspace/` is still what the executor works on. The identity lock records a fingerprint of the base harness, so rerunning in the same namespace with a different (or edited) `--base-harness` is refused rather than resumed: give each starting point its own `--tag` or `--runs`.

## Limitations and honesty

- **No results for this port.** The paper's gains were measured with Gemini 3.5 Flash and GLM-5 executors on professional, legal and office-work benchmarks (JobBench, LAB, WorkBuddy-Bench). Nothing has been published for Claude Code, Claude models or coding tasks, and this repository makes no claim that the method improves them.
- **Selection has no ground truth.** The selector compares anonymised deliverables and can prefer a worse one. Judging happens afterwards and is optional; a rubric judge is itself a model.
- **At `k=1` there is no selector.** The single final harness is adopted without being compared to the base. Run `harness2 judge --k 1 --bases` and compare before trusting it.
- **Every task costs real model calls,** several times over (see [Costs](#costs)).
- **The executor is not sandboxed.** It runs with all tools and `--permission-mode bypassPermissions` inside a copied workspace, with your user's file system and network access. Only run tasks you would let Claude Code run unattended.
- **Context policy (c2) is not editable.** Claude Code assembles its own context.
- **Libraries are scoped** per runs root, `k`, mode, seed and tag. They do not carry over between experiments; `export` is how an adopted harness leaves the loop.

## Attribution and license

Licensed under the [Apache License, Version 2.0](LICENSE). This is a port of [google-research/harness2](https://github.com/google-research/harness2), Copyright 2026 Google LLC, also Apache-2.0. The method and the improver prompts are adapted from that work; files that began as upstream code keep the upstream copyright header and carry a modification notice. See [NOTICE](NOTICE). This project is not affiliated with or endorsed by Google.

If you use this work, cite the paper:

```bibtex
@article{xu2026harness2,
  title   = {Harness$^2$: Recursive Agent Harnessing for an Open World},
  author  = {Xu, Ruiyao and Chen, Yanfei and CuiZhu, Zhongying and Dalvi Mishra, Bhavana and Ming, Yifei and Yu, Han and Han, Rujun and Lee, Chen-Yu and Pfister, Tomas},
  journal = {arXiv preprint arXiv:2610.14224},
  year    = {2026},
  url     = {https://arxiv.org/abs/2610.14224}
}
```

Contributions: see [CONTRIBUTING.md](CONTRIBUTING.md).
