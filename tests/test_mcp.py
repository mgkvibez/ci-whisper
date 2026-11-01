"""The MCP server: protocol shape, tool behaviour, and a real stdio round trip."""

import io
import json
import os
import subprocess
import sys

import pytest

from ci_whisper.mcp_server import handle, serve

FIXTURES = os.path.join(os.path.dirname(__file__), "fixtures")


def fixture(name):
    with open(os.path.join(FIXTURES, name), "r", encoding="utf-8") as fh:
        return fh.read()


def rpc(method, params=None, request_id=1):
    message = {"jsonrpc": "2.0", "method": method}
    if request_id is not None:
        message["id"] = request_id
    if params is not None:
        message["params"] = params
    return message


def test_initialize_reports_server_and_protocol():
    result = handle(rpc("initialize", {}))["result"]
    assert result["protocolVersion"]
    assert result["serverInfo"]["name"] == "ci-whisper"
    assert "tools" in result["capabilities"]


def test_initialized_notification_gets_no_response():
    assert handle(rpc("notifications/initialized", request_id=None)) is None


def test_ping_returns_empty_result():
    assert handle(rpc("ping"))["result"] == {}


def test_tools_list_advertises_both_tools_with_schemas():
    tools = {t["name"]: t for t in handle(rpc("tools/list"))["result"]["tools"]}
    assert set(tools) == {"read_ci_log", "read_github_run"}
    assert tools["read_ci_log"]["inputSchema"]["anyOf"]
    assert tools["read_github_run"]["inputSchema"]["required"] == ["run_id"]


def test_read_ci_log_finds_the_root_cause():
    result = handle(rpc("tools/call", {"name": "read_ci_log",
                                      "arguments": {"log": fixture("pytest_fail.log")}}))["result"]
    assert result["isError"] is False
    assert "First real error" in result["content"][0]["text"]
    root = result["structuredContent"]["root_cause"]
    assert root and root["line"] >= 1 and root["text"]


def test_read_ci_log_accepts_a_path():
    result = handle(rpc("tools/call", {"name": "read_ci_log",
                                      "arguments": {"path": os.path.join(FIXTURES, "traceback.log")}}))["result"]
    assert result["isError"] is False
    assert result["structuredContent"]["root_cause"]


def test_a_log_with_no_signal_is_an_error_result_not_a_crash():
    result = handle(rpc("tools/call", {"name": "read_ci_log",
                                      "arguments": {"log": fixture("noise_only.log")}}))["result"]
    assert result["isError"] is True
    assert "no real error" in result["content"][0]["text"].lower()


def test_missing_input_is_an_error_result():
    result = handle(rpc("tools/call", {"name": "read_ci_log", "arguments": {}}))["result"]
    assert result["isError"] is True


def test_bad_provider_is_rejected():
    result = handle(rpc("tools/call", {"name": "read_ci_log",
                                      "arguments": {"log": "x", "provider": "jenkins"}}))["result"]
    assert result["isError"] is True


def test_unknown_tool_is_an_invalid_params_error():
    response = handle(rpc("tools/call", {"name": "nope", "arguments": {}}))
    assert response["error"]["code"] == -32602


def test_unknown_method_is_method_not_found():
    assert handle(rpc("does/not/exist"))["error"]["code"] == -32601


def test_github_run_without_gh_reports_a_usable_message():
    result = handle(rpc("tools/call", {"name": "read_github_run",
                                      "arguments": {"run_id": "0"}}))["result"]
    assert result["isError"] is True
    assert "gh" in result["content"][0]["text"].lower()


def test_serve_answers_each_line_and_skips_the_notification():
    requests = [
        json.dumps(rpc("initialize", {}, 1)),
        json.dumps(rpc("notifications/initialized", request_id=None)),
        json.dumps(rpc("tools/list", {}, 2)),
        json.dumps(rpc("tools/call", {"name": "read_ci_log",
                                     "arguments": {"log": fixture("pytest_fail.log")}}, 3)),
    ]
    out = io.StringIO()
    serve(io.StringIO("\n".join(requests) + "\n"), out)
    responses = [json.loads(line) for line in out.getvalue().strip().splitlines()]
    assert [r["id"] for r in responses] == [1, 2, 3]
    assert responses[2]["result"]["isError"] is False


def test_serve_survives_a_malformed_line():
    out = io.StringIO()
    serve(io.StringIO("not json\n" + json.dumps(rpc("ping", {}, 1)) + "\n"), out)
    responses = [json.loads(line) for line in out.getvalue().strip().splitlines()]
    assert responses[0]["error"]["code"] == -32700
    assert responses[1]["id"] == 1


def test_stdio_round_trip_through_a_real_subprocess():
    """The transport is the thing most likely to break, so exercise it for real."""
    messages = "\n".join([
        json.dumps(rpc("initialize", {}, 1)),
        json.dumps(rpc("notifications/initialized", request_id=None)),
        json.dumps(rpc("tools/call", {"name": "read_ci_log",
                                     "arguments": {"log": fixture("npm_fail.log")}}, 2)),
    ]) + "\n"
    completed = subprocess.run(
        [sys.executable, "-m", "ci_whisper.mcp_server"],
        input=messages, capture_output=True, text=True, timeout=60,
        cwd=os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    )
    assert completed.returncode == 0, completed.stderr
    responses = [json.loads(line) for line in completed.stdout.strip().splitlines()]
    assert [r["id"] for r in responses] == [1, 2]
    assert responses[1]["result"]["isError"] is False
    assert "ready on stdio" in completed.stderr
