# Harness² for Claude Code — design and implementation contract

This document is the build contract. Every module, signature, on-disk path and CLI flag named
here is binding for implementers. Where it says "port", the upstream file is the reference and
its public signatures are kept verbatim unless this document says otherwise.

Upstream: https://github.com/google-research/harness2 (Apache-2.0, Google LLC, 2026),
paper arXiv:2610.14224 "Harness²: Recursive Agent Harnessing for an Open World".
A pinned checkout of upstream commit `1cfe254e` is available to implementers at the path given
in their brief (`UPSTREAM`). Read the upstream file before porting it.

## 1. What this project is

Harness² keeps the model fixed and improves the *harness*: the editable files around the
model. Upstream targets OpenCode and Codex and runs only on Vertex AI. This project is the
same method with exactly one substrate, **Claude Code** (`claude -p`), using the user's normal
Claude Code login (OAuth or `ANTHROPIC_API_KEY`). No Vertex, no Docker, no benchmarks. Tasks are
the user's own, in a simple on-disk format.

The method, unchanged from upstream:

- **Task-level recursion.** For one task: roll the base harness `k` times, then
  Propose (INV = how the agent works, DOM = what a practitioner knows) → Probe (roll each
  candidate once) → Compose (`agg1..aggk` in parallel mode, `c1..ck` in sequential mode).
  Selection picks among the finals by reading *deliverables only*, anonymised. No rubric, no
  score, no grader output ever reaches the improver or the selector.
- **Harnessing-level recursion.** After adoption, a reflection session writes the adopted
  harness plus an evidence-backed lesson into a per-domain library that later tasks in the same
  domain read before proposing.
- Budget identity per task and mode: R = 4k rollouts, P = 3k improver sessions (`core/ledger.py`).

Three roles, all `claude -p`:

| role | what it does | tools | default model |
|---|---|---|---|
| executor | runs the task inside a workspace with the harness installed | all, `bypassPermissions` | `claude-opus-5-5` |
| improver | proposer / aggregator / composer / selector / reflector; edits `context/harness/` in a temp dir | Read, Edit, Write, Glob, Grep (no Bash, no web) | `claude-opus-5-5` |
| judge | optional, after selection, scores a chosen run against a hidden rubric | Read only | `claude-opus-5-5` |

## 2. Repository layout

```
harness2/                       package (import name `harness2`)
  __init__.py                   __version__
  __main__.py                   python -m harness2 -> cli.main
  config.py                     env + .env, defaults, ConfigError, validate()
  tasks.py                      task discovery/loading over a tasks directory; new-task scaffold
  core/
    tree.py                     c1..c8 tree: read/validate/write/diff/diversity (port)
    spec.py                     ChannelSpec, BenchPrompts, BenchSpec, RunView, Protocols (port)
    prompts.py                  every prompt (port; surface text rewritten for Claude Code)
    context.py                  evidence tree builder (port, unchanged logic)
    proposer.py                 improver sessions over `claude -p` (port; claude_cli only)
    recursion.py                parallel / sequential (port, unchanged)
    selection.py                anonymised selection + reflection (port, unchanged)
    library.py                  per-domain experience library (port, unchanged)
    ledger.py                   budget identity (port, unchanged)
    runmark.py                  completion marker + harness fingerprint (port, unchanged)
    schedule.py                 namespaces, stream order, run_task/run_domain/run_sweep (port)
    judge.py                    judge the chosen run (port) + rubric judge via claude -p
    report.py                   summaries (port, simplified: one score line)
  substrate/
    __init__.py
    surface.py                  CLAUDE_CODE_SURFACE (SurfaceSpec) + render()/unrender()
    claude_code.py              ClaudeCodeSubstrate: execute(), trajectory renderer, deliverables
  adapters/
    __init__.py
    local.py                    LocalTasksAdapter: BenchmarkAdapter over a tasks directory
  demo.py                       deterministic demo adapter + scripted proposer (no model calls)
  cli/
    __init__.py                 `main(argv)`: argparse with subcommands
    _common.py                  shared arg groups and on-disk conventions
    run.py  judge.py  report.py  demo.py  check.py  task.py  export.py  library.py
  base_harness/                 the v0 harness tree shipped inside the package (see §5)
examples/
  tasks/                        a small example task set (3 tasks, 2 domains), see §6
tests/                          pytest; no network, no real `claude`
docs/
  DESIGN.md                     this file
  USAGE.md                      end-user guide (installed into README too)
README.md  LICENSE  NOTICE  pyproject.toml  .env.example  .gitignore
```

Python ≥ 3.11, **zero runtime dependencies** (stdlib only). `pytest` is the only dev dependency.
Ported files keep the upstream Apache header and add one line:
`# Modifications Copyright 2026 harness2-claude-code contributors.`

## 3. The Claude Code surface (c1..c8)

A harness is a directory tree. `harness2/substrate/surface.py` defines:

```python
CLAUDE_CODE_SURFACE = SurfaceSpec(
    name="claude_code",
    always_on=("CLAUDE.md", "guardrails.md", "MEMORY.md", "tool_guidance.md"),
    always_on_max=24_000,
    always_on_cap_reason="every token here is in context on every turn and competes with the agent's own output budget",
    skill_dir="skills",
    skill_name_rule="overrides_dir",      # Claude Code: `name:` optional; if present must equal the dir
    trigger_hint=('Fix it by adding a trigger line naming `{name}` to CLAUDE.md or guardrails.md, '
                  'e.g. "When <the situation this skill handles>, use the `{name}` skill."'),
    autotrigger_file="guardrails.md",
    trigger_line="- When {desc}, use the `{name}` skill.\n",
    subagent_dir="agents",                # NEW field: agents/<name>.md (Claude Code layout)
    subagent_requires_mode=False,
    plugin_dir=None,
    hooks_file="hooks.json",
    hook_events=frozenset({"PreToolUse","PostToolUse","UserPromptSubmit","Stop","SubagentStart",
                           "SubagentStop","SessionStart","SessionEnd","PreCompact","Notification",
                           "PermissionRequest"}),
    compile_skill_scripts=True,
    reject_empty_always_on=False,
    stubs=(
        ("scripts/README.md", "<!-- c7: deterministic helpers, run as `python3 scripts/<name>.py`. Add one by emitting scripts/<name>.py. -->\n"),
        ("agents/README.md",  "<!-- c5: subagents, invoked with the Agent tool in their OWN context. Add one as agents/<name>.md with frontmatter `name: <name>` and `description:`. -->\n"),
        ("skills/README.md",  "<!-- c6: reusable workflows loaded on demand. Add one as skills/<name>/SKILL.md with a `description:` (and optional `name:` matching the directory). -->\n"),
    ),
)
```

| harness path | component | installed into the executor workspace as |
|---|---|---|
| `CLAUDE.md` | c1 role and procedure | first section of `<ws>/CLAUDE.md` |
| `guardrails.md` | c6 always-on rules | `## Guardrails` section of `<ws>/CLAUDE.md` |
| `MEMORY.md` | c3 durable domain facts | `## Memory` section of `<ws>/CLAUDE.md` |
| `tool_guidance.md` | c4 tool prose | `## Tool guidance` section of `<ws>/CLAUDE.md` |
| `skills/<name>/SKILL.md` (+ `skills/<name>/scripts/*`) | c6 on-demand skill | `<ws>/.claude/skills/<name>/...` |
| `agents/<name>.md` | c5 subagent | `<ws>/.claude/agents/<name>.md` |
| `scripts/<name>.py` | c7 deterministic helper | `<ws>/scripts/<name>.py` |
| `hooks.json` | c8 hooks (tool-loop lever) | merged as `"hooks"` into `<ws>/.claude/settings.json` |
| (c2 context policy) | not injectable | Claude Code owns context assembly; same as upstream OpenCode |

`render(harness_dir, workspace) -> None` installs a harness tree into a workspace exactly as
above. Empty always-on files are skipped; if all four are empty, no `CLAUDE.md` is written
(v0 = bare Claude Code). `unrender(project_dir) -> Tree` is the inverse for `harness2 task import`
and `harness2 export` round-trips: it reads `CLAUDE.md` whole into `CLAUDE.md` (c1) and the other
three slots stay empty unless the file has the exact `## Guardrails` / `## Memory` /
`## Tool guidance` section headers written by `render`, in which case they are split back out.

Tree rules (`core/tree.py`, port of upstream with these deltas):

- `SurfaceSpec` gains `subagent_dir: str = "sub_agents"`; the subagent regex is
  `rf"{subagent_dir}/({_NAME})\.md"` when `subagent_dir == "agents"` and upstream's
  `sub_agents/<name>/AGENT.md` form otherwise. Only the Claude Code form is exercised here.
- Subagent frontmatter must have `description:`; a `name:` line, if present, must equal the
  file stem (Claude Code uses the `name:` field as the agent id).
- Skill frontmatter: `description:` required; `name:` optional but must equal the directory.
- Every skill directory name must appear verbatim in the always-on text (trigger line);
  `autotrigger()` repairs this before validation, as upstream does.
- `scripts/*.py` snake_case and must compile; skill scripts `.py` must compile.
- `hooks.json` validated against the Claude Code hook schema:
  `{"hooks": {"<Event>": [{"matcher": "<optional>", "hooks": [{"type": "command", "command": "...", "timeout": <optional int>}]}]}}`.
  Only `"type": "command"` entries are accepted.
- No Jinja-brace rule (Claude Code does not template these files).
- Every script a skill or always-on file invokes (`python3 scripts/x.py` or `python3 skills/<s>/scripts/x.py`) must exist in the tree. Reserved scripts: none.

## 4. Tasks (`harness2/tasks.py`)

A tasks directory is `<tasks>/<domain>/<task-id>/`:

```
task.json         required. {"instruction": "...", "deliverables": ["output/report.md"],
                             "timeout_s": 1800, "max_budget_usd": 10.0, "effort": "high"}
                  "instruction" may be omitted when instruction.md exists.
instruction.md    optional, the task prompt verbatim (preferred for long prompts)
workspace/        optional, files copied into the executor's working directory before the run
rubric.json       optional, hidden from the improver. {"criteria": ["...", "..."]}
check.py          optional, hidden. `python3 check.py <deliverables_dir>` prints JSON
                  {"n_passed": int, "n_criteria": int} ; takes precedence over rubric.json
```

- `task_id` is `"<domain>/<task-dir>"`. `domain` is the parent directory name.
- `discover(tasks_root) -> dict[str, str]` task_id → domain, sorted. Raises FileNotFoundError
  with a message pointing at `harness2 task new` when the root has no tasks.
- `load(tasks_root, task_id) -> Task` dataclass: `id, domain, dir, instruction, deliverables,
  timeout_s, max_budget_usd, effort, workspace (Path|None)`. Defaults: timeout 1800 s,
  budget 10.0 (`0` = do not pass `--max-budget-usd`), deliverables `["output/answer.md"]`,
  effort "" (unset: Claude Code default).
- `leak_globs = ("rubric.json", "check.py", "judged.json", "*.judged.json")` — never copied
  into any model context (enforced by upstream's `context.assert_no_leak_paths`).
- `scaffold(tasks_root, domain, name, *, instruction="") -> Path` writes a new task with
  `task.json`, `instruction.md`, an empty `workspace/` and a commented `rubric.json.example`.
- `split` support as upstream: `select(tasks, split, path)` with the same JSON split-file format
  `{"benchmark": "local", "train": [...], "test": [...]}` and `harness2 task split --seed N`.

## 5. Base harness

`harness2/base_harness/` ships v0 = **empty**: four always-on files containing only an HTML comment
describing the slot, plus the three README stubs. This matches upstream's v0 ("the bare stock
run"). `harness2 run --base-harness DIR` starts from another tree (for example one produced by
`harness2 task import --from <project>`, which runs `unrender` on an existing Claude Code project).

## 6. Example tasks (`examples/tasks/`)

Two domains, three tasks, each solvable by Claude Code in a few minutes with small workspaces.
No rubric is required; include one `rubric.json` and one `check.py` so the judge path is
exercised in docs. Suggested:

- `data-cleanup/ledger-reconcile`: a CSV of signed transactions plus a messy second CSV; deliver
  `output/reconciliation.md` with totals and unmatched rows. `check.py` verifies the totals.
- `data-cleanup/contacts-dedupe`: dedupe a contacts CSV into `output/contacts_clean.csv` with a
  short `output/notes.md`. `rubric.json` with 5 criteria.
- `writing/release-notes`: a git-log-style text file → `output/RELEASE_NOTES.md` grouped by
  type. `rubric.json` with 4 criteria.

## 7. Executor (`harness2/substrate/claude_code.py`)

```python
@dataclass
class ClaudeCodeSubstrate:
    name: str = "cc"
    model: str = config.EXECUTOR_MODEL
    effort: str = config.EXECUTOR_EFFORT          # "" means do not pass --effort
    claude_bin: str = config.CLAUDE_BIN
    def surface(self) -> SurfaceSpec
    def preflight(self) -> None                   # claude on PATH and `claude --version` works
    def execute(self, task: Task, harness_dir: Path | None, run_dir: Path, *, base_harness: Path, timeout: int | None = None) -> Path
    def trajectory(self, run_dir: Path, *, obs_trunc: int, max_chars: int = 400_000) -> str
    def delivered(self, run_dir: Path, deliverables: Sequence[str]) -> bool
    def deliverable_files(self, run_dir: Path, deliverables: Sequence[str]) -> dict[str, str]
    def deliverable_text(self, run_dir: Path, deliverables: Sequence[str]) -> str
```

`execute()` is idempotent per `run_dir` (reuses a run whose `.adapt_complete` marker matches the
harness fingerprint, via `core/runmark.py`). Steps:

1. `ws = run_dir / "workspace"`: fresh copy of `task.workspace` (if any), then
   `render(harness_dir or base_harness, ws)`; `mkdir ws/output`.
2. Command (verify every flag against `claude --help` at implementation time; drop any flag the
   installed CLI does not have and record that in `run_dir/command.json`):

   ```
   claude -p --output-format stream-json --verbose
          --permission-mode bypassPermissions
          --model <model> [--effort <effort>]
          --setting-sources project
          --no-session-persistence
          [--max-budget-usd <task.max_budget_usd>]     (omitted when it is 0)
   ```
   cwd = `ws`; the prompt (task instruction) is passed on **stdin**. Environment: a copy of
   `os.environ` with `CLAUDECODE` and `CLAUDE_CODE_ENTRYPOINT` removed (so a nested session is
   allowed when harness2 itself runs inside Claude Code). Nothing else is stripped or added.
   Wall timeout = `task.timeout_s`; on timeout the process group is killed and the run is
   recorded as an error, never raised.
3. stdout → `run_dir/stream.jsonl`; stderr → `run_dir/stderr.txt`; the final `result` event →
   `run_dir/result.json` (`total_cost_usd`, `num_turns`, `duration_ms`, `is_error`, `subtype`).
4. Declared deliverables that exist are copied from `ws/<path>` to `run_dir/deliverables/<path>`.
5. `runmark.mark_complete(run_dir, run_id=..., harness_dir=harness_dir, extra={"cost_usd": ...})`.

`trajectory()` renders `stream.jsonl` into upstream's grammar, one event per line:
`[ASSISTANT] text`, `[ACTION] ToolName({json args clipped to 400 chars})`,
`[OBSERVATION] ...` / `[OBSERVATION ERROR] ...` (tool_result with `is_error`),
`[ERROR] ...` (result `is_error` or stream parse errors), and a final
`[RESULT] subtype=<s> turns=<n> cost_usd=<c> duration_s=<d>`. Per-observation clip is `obs_trunc`
with the marker `…(+N chars — render truncation)`; whole-trajectory cap `max_chars`.
Stream-json shapes to handle: `{"type":"system","subtype":"init"}`, `{"type":"assistant",
"message":{"content":[{"type":"text"|"tool_use"|"thinking"}]}}`, `{"type":"user","message":
{"content":[{"type":"tool_result","content":str|list,"is_error":bool}]}}`, `{"type":"result"}`.
Be defensive: unknown events are skipped, never fatal.

`deliverable_files()` returns `{rel: text}` for each declared deliverable present under
`run_dir/deliverables/`; text files are read as UTF-8 with replacement; a file that is not
valid text is rendered as `(binary file, N bytes)`. `deliverable_text()` joins them as
`# <rel>\n\n<text>` blocks. `delivered()` is true when every declared deliverable exists and is
non-empty.

## 8. Improver channel (`harness2/core/proposer.py`)

Port upstream, keeping `Proposer`, `Reply`, `ProposerError`, `call(mission, context_dir, label,
*, system, timeout, max_attempts, harvest)`, the audit/cache/harvest logic and
`_call_fingerprint`. Deltas:

- Only the `claude_cli` channel exists. `_opencode` and bun/OpenCode code are removed.
- `env()`: copy of `os.environ` minus `CLAUDECODE` and `CLAUDE_CODE_ENTRYPOINT`. **Do not** strip
  `ANTHROPIC_API_KEY`/`ANTHROPIC_AUTH_TOKEN` and do not set any Vertex variable.
- Command:
  ```
  claude -p --output-format json
         --model <spec.model> --effort <spec.effort>
         --permission-mode bypassPermissions
         --tools Read,Edit,Write,Glob,Grep
         --setting-sources project --no-session-persistence
         [--max-budget-usd <config.IMPROVER_MAX_BUDGET_USD>]   (omitted when it is 0)
         [--append-system-prompt <system>]
  ```
  mission on stdin, cwd = the temp session dir containing `context/`. If `--tools` is not
  accepted by the installed CLI, fall back to `--disallowedTools Bash WebFetch WebSearch Agent`.
  Verify at implementation time.
- `ChannelSpec.kind` accepts only `"claude_cli"`; `resolve_channel_model` keeps its signature.
- Concurrency semaphore `config.PROPOSER_CONCURRENCY` (default 2) as upstream.

## 9. Adapter (`harness2/adapters/local.py`)

`LocalTasksAdapter(tasks_root, *, substrate: ClaudeCodeSubstrate, base_harness: Path, split_file=None, runs_root: Path)`
implements upstream's `BenchmarkAdapter` protocol exactly, with `spec = BenchSpec(name="local",
install_mode="workspace_config", prompts=BenchPrompts(...), channels={"proposer": ..., "selector": ...},
leak_globs=tasks.LEAK_GLOBS, allow_subagents=True, obs_trunc=50_000)`.

`BenchPrompts` defaults for local tasks (the only benchmark-shaped text, must not contain the
words benchmark/evaluation/grader/rubric/score):

- `work`: "complete a self-contained piece of work inside a project directory with Claude Code: read the instructions and the files provided, do the work with the available tools, and write every requested deliverable to the path the instructions name"
- `domain_word`: "domain", `deliverable_word`: "deliverable"
- `inv_levers`, `dom_levers`, `yardstick`, `domain_guidance`, `selection_checks`: empty.
- A task directory may override any field with an optional `prompts.json` (same keys).

Adapter methods map to `tasks.py` and the substrate. `execute(task_id, harness_dir, run_id)`
uses `run_dir = runs_root / "rollouts" / run_id` where `run_id = "<ns>/<task-id>/<label>"` comes
from `schedule.Namespace.run_id` (task ids contain one `/`, which becomes a directory level:
`<runs>/rollouts/local-cc-agg1/data-cleanup/ledger-reconcile/base1/`). `score(run_dir, task_id)`
runs `check.py` if present, else the rubric judge (§11), else returns `None`.

## 10. Prompts (`harness2/core/prompts.py`)

Port upstream verbatim **except**: replace the OpenCode/Codex surface blocks (`_SURFACE_OPENCODE`,
`_TTS_SURFACE_HEAD_OPENCODE`, the `_INV_TEMPLATE` loop-lever sentence and the `_DOM_TEMPLATE`
trigger sentence) with one `_SURFACE_CLAUDE_CODE` block and one `_TTS_SURFACE_HEAD_CLAUDE_CODE`
block that describe the table in §3 in the same voice and with the same hard rules, adapted:

- skills load on demand via the Skill tool; the directory name must appear in `CLAUDE.md` or `guardrails.md`;
- subagents are `agents/<name>.md` with frontmatter `name:` and `description:`, invoked with the Agent tool, own context, return text;
- hooks are `hooks.json` in Claude Code's hook schema (events listed in §3); only `"type": "command"`; PreToolUse can deny with a JSON decision, PostToolUse can feed a reason back; put logic in a `scripts/` helper the hook calls;
- "NEVER instruct the agent to read `.claude/skills/.../SKILL.md` off the filesystem" stays;
- the Jinja rule is dropped; the three-rules block becomes two rules (trigger line, scripts must exist) plus "hooks.json must be valid JSON in the hook schema".

Keep `GENERALIZE`, `_TTS_*`, `aggregate`, `compose_round`, `select`, `experience`, `ANSWER_FREE*`,
`EVIDENCE_BOUNDARY*`, `POOL_*` byte-for-byte. `_SURFACE_TEXTS` has exactly one key, `"claude_code"`.

## 11. Judge and report

`core/judge.py`: port `Scored`, `judge_one`, `judge_selections`, `write`. Add
`rubric_judge(deliverable_text: str, instruction: str, criteria: list[str], *, model, claude_bin) -> dict | None`
which runs `claude -p --output-format json --tools "" --model <judge>` with a prompt that lists
the criteria and asks for a fenced ```json block `{"results": [{"criterion": ..., "pass": bool, "why": ...}]}`;
returns `{"n_passed", "n_criteria", "all_pass", "per_criterion"}` or `None` on any failure.
`core/report.py`: port, one score line per (mode, k): `score %`, picks (final/base), fell back,
ungraded, USD.

## 12. Scheduling and on-disk layout (`core/schedule.py`, port)

Namespace string: upstream `ns()` with benchmark `local` and substrate `cc`, so
`local-cc-<mode><k>[-s<seed>][-<tag>]`, e.g. `local-cc-agg1`.

```
<runs>/
  adapt/local-cc-agg<k>[...]/<domain>__<task>/  summary.json (both modes), harnesses/<label>/, context/<label>/,
                                         selection/<mode><k>/selection.json, reflection/<mode><k>/,
                                         reflection/<mode><k>.experience/
  adapt/local-cc-seq<k>[...]/<domain>__<task>/
  adapt/<summary ns>/config.json         run identity lock (method version, models, efforts, split,
                                         task stream, seed, max_tasks, domains, base-harness fingerprint)
  adapt/<summary ns>/judged.json         harness2 judge; base_judged.json with --bases
  adapt/_audit/<summary ns>/             improver sessions of both modes: <label>.<key>.{mission.md,
                                         response.md,meta.json,workspace/}
  adapt/_sweeps/<summary ns>-<stamp>.json
  rollouts/<run_id>/                     one rollout (run_id = <ns>/<domain>/<task>/<label>): workspace/,
                                         command.json, stream.jsonl, trajectory.md, result.json,
                                         deliverables/, adapter.json, .adapt_complete
  library/local/k<k>-cc[-s<seed>][-<tag>]/<mode>/<domain>/INDEX.md, harnesses/<domain>__<task>/{harness/,EXPERIENCE.md}
```

`<summary ns>` is always the parallel (`agg`) namespace; base rollouts live there too.

`run_task`, `run_domain`, `run_sweep`, `task_stream`, `stream_order`, `_resume` and
`_write_summary` are ported unchanged (flock on POSIX only; on other platforms skip locking).

## 13. Configuration (`harness2/config.py`)

Read from the shell, then `.env` in the current directory (shell wins). All optional.

| variable | default | meaning |
|---|---|---|
| `HARNESS2_EXECUTOR_MODEL` | `claude-opus-5-5` | executor `--model` |
| `HARNESS2_EXECUTOR_EFFORT` | `` | executor `--effort`; empty = CLI default |
| `HARNESS2_IMPROVER_MODEL` | `claude-opus-5-5` | improver/selector/reflector model |
| `HARNESS2_IMPROVER_EFFORT` | `high` | improver `--effort` |
| `HARNESS2_IMPROVER_MAX_BUDGET_USD` | `25` | per improver session; `0` = do not pass `--max-budget-usd` |
| `HARNESS2_JUDGE_MODEL` | `claude-opus-5-5` | rubric judge model |
| `HARNESS2_RUNS_DIR` | `runs` | output root |
| `HARNESS2_TASKS_DIR` | `tasks` | tasks root |
| `HARNESS2_CLAUDE_BIN` | `claude` | CLI binary |
| `HARNESS2_PHASE_WIDTH` | `2` | concurrent rollouts per phase |
| `HARNESS2_PROPOSER_CONCURRENCY` | `2` | concurrent improver sessions |
| `HARNESS2_TRANSPORT_ATTEMPTS` | `2` | retries per improver call |

`validate()` raises `ConfigError` with a one-line remediation for a bad effort level, a
non-positive width, a negative improver budget, or a missing `claude` binary. Model ids are bare
Anthropic ids (no `/`).

`--max-budget-usd` is checked by Claude Code between turns, so a session can overshoot it (a
live `--max-budget-usd 0.50` session ended at $1.60). A session that hits it ends with
`is_error: true`, `subtype: "error_max_budget_usd"` and no `result`; for the improver that is a
failed session (its edits are discarded, the `ProposerError` names the spend), retried up to
`HARNESS2_TRANSPORT_ATTEMPTS` times, after which the stage falls back to its input harness.
Hence the generous default; `0` removes the cap.

## 14. CLI (`harness2/cli`)

One console script `harness2` (and `python -m harness2`). Subcommands:

```
harness2 run     [--tasks DIR] [--runs DIR] --k K [--mode parallel|sequential|both]
                 [--workers N] [--only-domains a,b] [--only-tasks id,id] [--max-tasks N]
                 [--seed N] [--split all|train|test --split-file F] [--base-harness DIR]
                 [--tag T] [--dry-run]
harness2 judge   [--tasks DIR] [--runs DIR] --k K [--mode ...] [--seed N] [--tag T] [--bases]
                 [--parallel N]
harness2 report  [--runs DIR] --k K [--mode ...] [--seed N] [--tag T]
harness2 demo    [--output DIR] [--k K] [--mode ...]            # no model calls
harness2 check   [--tasks DIR]                                   # preflight; zero tasks is a note, exit 0
harness2 task new <domain> <name> [--tasks DIR] [--instruction TEXT]
harness2 task list [--tasks DIR]
harness2 task split --seed N --output F [--train-frac 0.5] [--tasks DIR]
harness2 task import --from PROJECT_DIR --dest HARNESS_DIR      # unrender an existing project
harness2 export  [--runs DIR] --task ID --k K [--mode parallel] [--seed N] [--tag T] --dest PROJECT_DIR [--force]
harness2 library show [--runs DIR] --k K [--mode parallel] [--domain D] [--seed N] [--tag T]
harness2 render  HARNESS_DIR WORKSPACE_DIR                       # install a tree into a workspace
```

`run` prints the stream and namespaces in `--dry-run` mode and exits without touching models.
A real run calls `config.validate()` and `substrate.preflight()` first, writes/validates the
identity lock, builds the `Proposer`, and calls `schedule.run_sweep`. Exit codes: 0 ok,
1 a domain died or zero tasks, 2 some tasks failed.

`export` copies the adopted harness of the chosen label into a project: `CLAUDE.md` is created
or, with an existing one, the harness sections are appended under a `<!-- harness2 -->` marker;
`.claude/agents`, `.claude/skills`, `scripts/` are copied; `hooks.json` is merged into
`.claude/settings.json` (existing keys kept). Refuses to overwrite an existing file unless
`--force`, and prints what it wrote.

## 15. Demo (`harness2/demo.py`)

Port upstream's deterministic demo: `DemoAdapter` with the two `ledger-*` tasks whose base
harness carries a buggy `scripts/summarize.py`, and `DemoProposer` with scripted replies. The
demo adapter uses the Claude Code surface (always-on file `CLAUDE.md` instead of
`systemprompt.md`) and executes scripts with the current Python interpreter. `harness2 demo`
writes `demo.json`; the integration test asserts the ledger identity (R=4k, P=3k), the chosen
totals (`85.15`, `20.25`), resume behaviour, and library prefix behaviour, as upstream's
`tests/test_workflow.py` does.

## 16. Tests

- `tests/test_tree.py` — surface, validate rules (each rule has one failing case), autotrigger,
  read/write round trip, diff, diversity, hooks schema.
- `tests/test_surface.py` — render/unrender round trip into a workspace layout.
- `tests/test_tasks.py` — discover/load/scaffold/split/leak globs.
- `tests/test_substrate.py` — executor with a **fake `claude`** script placed first on PATH that
  emits a canned stream-json (text, tool_use, tool_result with is_error, result) and writes the
  deliverable; trajectory rendering; idempotent reuse via the marker; timeout handling.
- `tests/test_proposer.py` — fake `claude` emitting `--output-format json`; harvest of
  `context/harness`; cache hit; empty-reply retry; ProposerError.
- `tests/test_workflow.py` — port of upstream's demo-based regression tests.
- `tests/test_cli.py` — `harness2 demo`, `task new/list`, `run --dry-run`, `export` on demo output.

No test may call the real `claude` binary or the network.

## 17. Costs and limits the docs must state

Per task at k: 4k executor runs + 3k improver sessions (+1 selector session when k>1, +1
reflection session). With Opus 5.5 on a subscription the executor runs count against the plan;
with an API key they are billed. `--max-tasks 1 --k 1 --dry-run` first. The user's global
`~/.claude/CLAUDE.md` is still loaded by the executor (Claude Code auto-discovery); hooks from
user settings are not, because `--setting-sources project` is passed. `--bare` is **not** used
because it disables OAuth/keychain auth.
