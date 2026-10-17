"""A stand-in for the `claude` CLI, for tests. Stdlib only; never touches the network.

Behaviour is steered by environment variables (see `fakes.fake_env`):

  FAKE_CLAUDE_REPLY    -p json: the reply text (default "<notes>KEEP_BASE</notes>")
  FAKE_CLAUDE_EDIT     -p json: "<relpath>=<content>" applied under ./context/harness when it
                       exists; "<relpath>=" deletes the file
  FAKE_CLAUDE_FAIL=1   result with is_error true, subtype error_during_execution
  FAKE_CLAUDE_BUDGET=1 -p json: result with is_error true, subtype error_max_budget_usd, no
                       `result` key and total_cost_usd 1.6 (a cap overshot between turns)
  FAKE_CLAUDE_EMPTY=1  -p json: a successful result with empty text
  FAKE_CLAUDE_HANG=1   sleep 60 s (stream-json: after the init event)
  FAKE_CLAUDE_DELIVER  stream-json: comma-separated files written under cwd
                       (default "output/answer.md")
  FAKE_CLAUDE_LOG      a directory; every -p call appends one JSON line to calls.jsonl
                       with argv, cwd, stdin and the environment's variable names
  FAKE_CLAUDE_HELP_OMIT  comma-separated long flags missing from --help and rejected

Event shapes mirror a real `claude -p --output-format stream-json --verbose` session
(Claude Code 2.1.302): system/init, assistant messages whose content holds thinking/text/
tool_use blocks, user messages holding tool_result blocks (content a string or a list of
text blocks), unrelated events such as rate_limit_event, and a final result event.
"""

from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path

VERSION = "9.9.9 (fake)"

# Long flags this fake accepts, with the number of values each takes ("*" = variadic).
FLAGS: dict[str, int | str] = {
    "--print": 0,
    "--output-format": 1,
    "--verbose": 0,
    "--permission-mode": 1,
    "--model": 1,
    "--effort": 1,
    "--setting-sources": 1,
    "--no-session-persistence": 0,
    "--max-budget-usd": 1,
    "--tools": "*",
    "--append-system-prompt": 1,
    "--disallowedTools": "*",
}

HELP = """Usage: claude [options] [command] [prompt]

Claude Code (fake) - starts an interactive session by default, use -p/--print for
non-interactive output

Options:
  --append-system-prompt <prompt>       Append a system prompt to the default system prompt
  --disallowedTools, --disallowed-tools <tools...>
      Comma or space-separated list of tool names to deny
  --effort <level>                      Effort level for the current session
  -h, --help                            Display help for command
  --max-budget-usd <amount>             Maximum dollar amount to spend on API calls
  --model <model>                       Model for the current session
  --no-session-persistence              Disable session persistence
  --output-format <format>              Output format: "text", "json", "stream-json"
  --permission-mode <mode>              Permission mode to use for the session
  -p, --print                           Print response and exit
  --setting-sources <sources>           Comma-separated list of setting sources to load
  --tools <tools...>                    Specify the list of available tools
  --verbose                             Override verbose mode setting from config
  -v, --version                         Output the version number
"""


def _omitted() -> set[str]:
    return {f.strip() for f in os.environ.get("FAKE_CLAUDE_HELP_OMIT", "").split(",") if f.strip()}


def _help() -> str:
    omit = _omitted()
    return (
        "\n".join(line for line in HELP.splitlines() if not any(f"{f} " in line for f in omit))
        + "\n"
    )


def _die(msg: str) -> None:
    sys.stderr.write(f"error: {msg}\n")
    sys.exit(1)


def _parse(argv: list[str]) -> dict[str, list[str] | bool]:
    opts: dict[str, list[str] | bool] = {}
    omit = _omitted()
    i = 0
    while i < len(argv):
        a = argv[i]
        if a == "-p":
            a = "--print"
        if a not in FLAGS or a in omit:
            _die(f"unknown option '{argv[i]}'")
        arity = FLAGS[a]
        i += 1
        if arity == 0:
            opts[a] = True
            continue
        values = []
        if arity == 1 and i < len(argv):
            values.append(argv[i])  # a single value may itself start with "-"
            i += 1
        while arity == "*" and i < len(argv) and not argv[i].startswith("-"):
            values.append(argv[i])
            i += 1
        if not values:
            _die(f"option '{a}' argument missing")
        opts[a] = values
    return opts


def _log(argv: list[str], stdin: str) -> None:
    d = os.environ.get("FAKE_CLAUDE_LOG")
    if not d:
        return
    Path(d).mkdir(parents=True, exist_ok=True)
    rec = {
        "argv": argv,
        "cwd": os.getcwd(),
        "stdin": stdin,
        "env_keys": sorted(os.environ),
        "pid": os.getpid(),
        "pgid": os.getpgid(0),
    }
    with open(Path(d) / "calls.jsonl", "a", encoding="utf-8") as fh:
        fh.write(json.dumps(rec) + "\n")


def _emit(ev: dict) -> None:
    sys.stdout.write(json.dumps(ev) + "\n")
    sys.stdout.flush()


def _apply_edit() -> None:
    spec = os.environ.get("FAKE_CLAUDE_EDIT")
    root = Path.cwd() / "context" / "harness"
    if not spec or "=" not in spec or not root.is_dir():
        return
    rel, _, content = spec.partition("=")
    target = root / rel.strip()
    if content == "":
        if target.is_file():
            target.unlink()
        return
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(content, encoding="utf-8")


def _result(text: str, *, cost: float, turns: int, error: bool = False) -> dict:
    ev = {
        "type": "result",
        "subtype": "error_during_execution" if error else "success",
        "is_error": error,
        "duration_ms": 10,
        "duration_api_ms": 8,
        "num_turns": turns,
        "result": text,
        "stop_reason": "end_turn",
        "session_id": "00000000-fake",
        "total_cost_usd": cost,
        "terminal_reason": "completed",
        "permission_denials": [],
    }
    if error:
        ev["errors"] = ["fake failure requested by FAKE_CLAUDE_FAIL"]
        ev.pop("result")
    return ev


def _json_mode() -> int:
    if os.environ.get("FAKE_CLAUDE_HANG") == "1":
        time.sleep(60)
    if os.environ.get("FAKE_CLAUDE_FAIL") == "1":
        print(json.dumps(_result("", cost=0.01, turns=1, error=True)))
        return 1
    if os.environ.get("FAKE_CLAUDE_BUDGET") == "1":
        _apply_edit()  # the session edited, then hit the cap: its edits must not be used
        ev = _result("", cost=1.6, turns=3, error=True)
        ev["subtype"] = "error_max_budget_usd"
        ev["errors"] = ["Reached maximum budget ($0.5)"]
        print(json.dumps(ev))
        return 1
    _apply_edit()
    text = (
        ""
        if os.environ.get("FAKE_CLAUDE_EMPTY") == "1"
        else os.environ.get("FAKE_CLAUDE_REPLY", "<notes>KEEP_BASE</notes>")
    )
    print(json.dumps(_result(text, cost=0.0123, turns=1)))
    return 0


def _assistant(*blocks: dict) -> dict:
    return {
        "type": "assistant",
        "message": {
            "model": "fake",
            "id": "msg_fake",
            "type": "message",
            "role": "assistant",
            "content": list(blocks),
            "stop_reason": None,
        },
        "parent_tool_use_id": None,
        "session_id": "00000000-fake",
    }


def _user(block: dict) -> dict:
    return {
        "type": "user",
        "message": {"role": "user", "content": [block]},
        "parent_tool_use_id": None,
        "session_id": "00000000-fake",
    }


def _stream_mode(opts: dict) -> int:
    cwd = os.getcwd()
    model = (opts.get("--model") or ["default"])[0]
    _emit(
        {
            "type": "system",
            "subtype": "init",
            "cwd": cwd,
            "session_id": "00000000-fake",
            "model": model,
            "tools": ["Bash", "Edit", "Read", "Write"],
            "permissionMode": (opts.get("--permission-mode") or ["default"])[0],
            "claude_code_version": VERSION,
        }
    )
    if os.environ.get("FAKE_CLAUDE_HANG") == "1":
        time.sleep(60)
    deliver = os.environ.get("FAKE_CLAUDE_DELIVER", "output/answer.md")
    paths = [p.strip() for p in deliver.split(",") if p.strip()]
    first = paths[0] if paths else "output/answer.md"
    _emit(
        _assistant(
            {"type": "thinking", "thinking": "", "signature": "c2ln"},
            {"type": "text", "text": "I will read the task and write the deliverable."},
        )
    )
    _emit({"type": "rate_limit_event", "rate_limit_info": {"status": "allowed"}})
    _emit(
        _assistant(
            {
                "type": "tool_use",
                "id": "toolu_1",
                "name": "Write",
                "input": {"file_path": os.path.join(cwd, first), "content": "draft"},
            }
        )
    )
    _emit(
        _user(
            {
                "tool_use_id": "toolu_1",
                "type": "tool_result",
                "content": f"File created successfully at: {os.path.join(cwd, first)}",
                "is_error": False,
            }
        )
    )
    _emit(
        _assistant(
            {
                "type": "tool_use",
                "id": "toolu_2",
                "name": "Bash",
                "input": {"command": "cat missing.txt", "description": "Read a missing file"},
            }
        )
    )
    _emit(
        _user(
            {
                "tool_use_id": "toolu_2",
                "type": "tool_result",
                "content": [
                    {"type": "text", "text": "cat: missing.txt: No such file or directory"}
                ],
                "is_error": True,
            }
        )
    )
    for rel in paths:
        target = Path(cwd) / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(f"fake deliverable for {rel}\n", encoding="utf-8")
    _emit(_assistant({"type": "text", "text": "Done."}))
    fail = os.environ.get("FAKE_CLAUDE_FAIL") == "1"
    _emit(_result("Done.", cost=0.05, turns=3, error=fail))
    return 1 if fail else 0


def main(argv: list[str]) -> int:
    if argv in (["--version"], ["-v"]):
        print(VERSION)
        return 0
    if argv in (["--help"], ["-h"]):
        sys.stdout.write(_help())
        return 0
    opts = _parse(argv)
    if "--print" not in opts:
        _die("this fake only supports -p/--print")
    stdin = sys.stdin.read()
    _log(argv, stdin)
    fmt = (opts.get("--output-format") or ["text"])[0]
    if fmt == "json":
        return _json_mode()
    if fmt == "stream-json":
        if "--verbose" not in opts:
            _die("When using --print, --output-format=stream-json requires --verbose")
        return _stream_mode(opts)
    print(os.environ.get("FAKE_CLAUDE_REPLY", "<notes>KEEP_BASE</notes>"))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
