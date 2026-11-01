"""An MCP server, so an agent can read a failed CI run instead of a human pasting logs.

Speaks JSON-RPC 2.0 over stdio, newline-delimited: that is the MCP stdio transport.
There is no dependency on the `mcp` package, because zero runtime dependencies is this
package's whole claim and the protocol surface needed here is five methods.

stdout carries protocol messages and nothing else. A stray print on stdout corrupts the
stream for the client, so every diagnostic goes to stderr.

Two tools. `read_ci_log` is the analysis the CLI already does, exposed as a tool.
`read_github_run` shells out to `gh run view --log-failed` so the agent does not have to
be handed a log at all. Neither one opens a pull request: the agent decides what to do
with a diagnosis, and this server does not hold a token.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from typing import Any, BinaryIO, Dict, List, Optional, TextIO, Tuple

from . import __version__
from .parse import diagnose

PROTOCOL_VERSION = "2024-11-05"
SERVER_NAME = "ci-whisper"

PARSE_ERROR = -32700
INVALID_REQUEST = -32600
METHOD_NOT_FOUND = -32601
INVALID_PARAMS = -32602

_LOG_SCHEMA = {
    "type": "object",
    "properties": {
        "log": {"type": "string", "description": "The raw CI log text."},
        "path": {"type": "string", "description": "Path to a log file, if not inline."},
        "provider": {
            "type": "string",
            "enum": ["auto", "github", "gitlab"],
            "description": "Log format. Defaults to auto-detection.",
        },
    },
    "anyOf": [{"required": ["log"]}, {"required": ["path"]}],
}

TOOLS: List[Dict[str, Any]] = [
    {
        "name": "read_ci_log",
        "description": (
            "Find the first real error in a failed CI log, the chain of cause, and a "
            "suggested fix. Use this on a failing run before changing any code."
        ),
        "inputSchema": _LOG_SCHEMA,
    },
    {
        "name": "read_github_run",
        "description": (
            "Fetch a failed GitHub Actions run's log with the gh CLI, then analyse it. "
            "Requires gh to be installed and authenticated."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "run_id": {"type": "string", "description": "The Actions run id."},
                "repo": {
                    "type": "string",
                    "description": "owner/name, if not the current directory's repo.",
                },
            },
            "required": ["run_id"],
        },
    },
]


class ToolError(Exception):
    """A tool failed in a way the caller should see as a result, not a crash."""


def _text(payload: str, is_error: bool = False,
          structured: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    result: Dict[str, Any] = {"content": [{"type": "text", "text": payload}],
                              "isError": is_error}
    if structured is not None:
        result["structuredContent"] = structured
    return result


def _format(diagnosis: Any) -> str:
    root = diagnosis.root_cause
    if root is None:
        return ("No real error found in %d lines. The log carries no failure signal; "
                "check whether the job failed before it produced output."
                % diagnosis.scanned)
    lines = ["First real error, line %d: %s" % (root.line_no, root.text)]
    if diagnosis.chain:
        lines.append("")
        lines.append("Causal chain:")
        for hit in diagnosis.chain:
            lines.append("  line %d  [%s]  %s" % (hit.line_no, hit.kind, hit.text))
    if diagnosis.phase:
        lines.append("")
        lines.append("Phase: %s" % diagnosis.phase)
    if diagnosis.fix:
        lines.append("")
        lines.append("Suggested fix: %s" % diagnosis.fix)
    return "\n".join(lines)


def _read_log(arguments: Dict[str, Any]) -> Dict[str, Any]:
    raw = arguments.get("log")
    path = arguments.get("path")
    provider = arguments.get("provider") or "auto"
    if provider not in ("auto", "github", "gitlab"):
        raise ToolError("provider must be one of auto, github, gitlab")
    if raw is None and path is None:
        raise ToolError("give either log or path")
    if raw is None:
        if not os.path.exists(path):
            raise ToolError("no such file: %s" % path)
        with open(path, "r", encoding="utf-8", errors="replace") as handle:
            raw = handle.read()
    if not isinstance(raw, str):
        raise ToolError("log must be a string")
    diagnosis = diagnose(raw, provider=provider)
    return _text(_format(diagnosis), diagnosis.root_cause is None, diagnosis.as_dict())


def _read_github_run(arguments: Dict[str, Any]) -> Dict[str, Any]:
    run_id = arguments.get("run_id")
    if not run_id:
        raise ToolError("run_id is required")
    command = ["gh", "run", "view", str(run_id), "--log-failed"]
    repo = arguments.get("repo")
    if repo:
        command.extend(["-R", str(repo)])
    try:
        completed = subprocess.run(command, capture_output=True, text=True, timeout=180)
    except FileNotFoundError:
        raise ToolError("the gh CLI is not installed; pass the log with read_ci_log instead")
    except subprocess.TimeoutExpired:
        raise ToolError("gh run view timed out after 180s")
    if completed.returncode != 0:
        detail = (completed.stderr or completed.stdout or "").strip()
        raise ToolError("gh run view failed (exit %s): %s"
                        % (completed.returncode, detail[:500] or "no output"))
    if not completed.stdout.strip():
        raise ToolError("run %s produced no failed-step log" % run_id)
    return _read_log({"log": completed.stdout})


_HANDLERS = {
    "read_ci_log": _read_log,
    "read_github_run": _read_github_run,
}


def call_tool(name: str, arguments: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """Run one tool. Raises KeyError for an unknown name, ToolError for a bad call."""
    if name not in _HANDLERS:
        raise KeyError(name)
    return _HANDLERS[name](arguments or {})


def _result(request_id: Any, result: Dict[str, Any]) -> Dict[str, Any]:
    return {"jsonrpc": "2.0", "id": request_id, "result": result}


def _error(request_id: Any, code: int, message: str) -> Dict[str, Any]:
    return {"jsonrpc": "2.0", "id": request_id, "error": {"code": code, "message": message}}


def handle(message: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """One request in, one response out. Returns None for notifications."""
    request_id = message.get("id")
    is_notification = "id" not in message
    method = message.get("method")
    params = message.get("params") or {}

    if not isinstance(method, str) or not method:
        return None if is_notification else _error(request_id, INVALID_REQUEST, "no method")

    if method == "initialize":
        return _result(request_id, {
            "protocolVersion": PROTOCOL_VERSION,
            "capabilities": {"tools": {"listChanged": False}},
            "serverInfo": {"name": SERVER_NAME, "version": __version__},
        })

    if method == "ping":
        return _result(request_id, {})

    if method == "tools/list":
        return _result(request_id, {"tools": TOOLS})

    if method == "tools/call":
        if not isinstance(params, dict):
            return _error(request_id, INVALID_PARAMS, "params must be an object")
        name = params.get("name")
        arguments = params.get("arguments") or {}
        try:
            return _result(request_id, call_tool(str(name), arguments))
        except KeyError:
            return _error(request_id, INVALID_PARAMS, "unknown tool: %s" % name)
        except ToolError as exc:
            return _result(request_id, _text(str(exc), True))

    if method.startswith("notifications/"):
        return None

    return None if is_notification else _error(
        request_id, METHOD_NOT_FOUND, "method not found: %s" % method)


def serve(stdin: TextIO, stdout: TextIO) -> int:
    """Read newline-delimited requests until EOF, answering each."""
    for line in stdin:
        line = line.strip()
        if not line:
            continue
        try:
            message = json.loads(line)
        except json.JSONDecodeError as exc:
            stdout.write(json.dumps(_error(None, PARSE_ERROR, "invalid json: %s" % exc)) + "\n")
            stdout.flush()
            continue
        if not isinstance(message, dict):
            stdout.write(json.dumps(_error(None, INVALID_REQUEST, "expected an object")) + "\n")
            stdout.flush()
            continue
        response = handle(message)
        if response is not None:
            stdout.write(json.dumps(response) + "\n")
            stdout.flush()
    return 0


def main(argv: Optional[List[str]] = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    if args and args[0] in ("--version", "-V"):
        print("%s-mcp %s" % (SERVER_NAME, __version__))
        return 0
    if args and args[0] in ("--help", "-h"):
        print("Usage: ci-whisper-mcp\n\nSpeaks MCP over stdio. Point your client at this command.")
        return 0
    print("ci-whisper-mcp %s ready on stdio" % __version__, file=sys.stderr)
    return serve(sys.stdin, sys.stdout)


if __name__ == "__main__":
    sys.exit(main())
