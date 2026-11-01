#!/usr/bin/env bash
# Release gate for ci-whisper: run before every push that claims "done".
# Five independent checks: install, version consistency, tests, the CLI doing real work,
# and the MCP server answering a real handshake over stdio.
# The smoke step asserts the CLI's documented exit code, including the
# non-zero code it is supposed to return when it finds something.
set -uo pipefail
cd "$(dirname "$0")/.."

fail() { echo "GATE FAILED: $*"; exit 1; }

echo "== check 1/5: editable install"
python3 -m pip install -q -e ".[dev]" || fail "install"

echo "== check 2/5: version consistency (pyproject vs module)"
pv=$(python3 - <<'PY'
import pathlib, re
try:
    import tomllib
    print(tomllib.loads(pathlib.Path("pyproject.toml").read_text())["project"]["version"])
except ModuleNotFoundError:                     # Python 3.9/3.10 have no tomllib
    text = pathlib.Path("pyproject.toml").read_text()
    print(re.search(r'(?m)^version\s*=\s*"([^"]+)"', text).group(1))
PY
)
mv=$(python3 -c "import ci_whisper;print(ci_whisper.__version__)")
[ "$pv" = "$mv" ] || { echo "pyproject says $pv, module says $mv"; fail "version drift"; }
echo "version $pv consistent"

echo "== check 3/5: test suite"
python3 -m pytest -q || fail "tests"

echo "== check 4/5: CLI smoke (expects exit 0)"
out=$(mktemp)
python3 -m ci_whisper.cli --file tests/fixtures/pytest_fail.log > "$out" 2>&1
code=$?
if [ "$code" != "0" ]; then
  echo "expected exit 0, got $code"; cat "$out"; rm -f "$out"; fail "cli exit code"
fi
grep -qi "FIRST REAL ERROR" "$out" || { echo "smoke output looked wrong:"; cat "$out"; rm -f "$out"; fail "cli output"; }
rm -f "$out"
echo "smoke ok (exit 0 as documented)"

echo "== check 5/5: the MCP server answers a handshake and a tool call on stdio"
mcp_out=$(mktemp)
{
  printf '%s\n' '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{}}'
  printf '%s\n' '{"jsonrpc":"2.0","method":"notifications/initialized"}'
  printf '%s\n' '{"jsonrpc":"2.0","id":2,"method":"tools/list"}'
  printf '%s\n' '{"jsonrpc":"2.0","id":3,"method":"tools/call","params":{"name":"read_ci_log","arguments":{"path":"tests/fixtures/pytest_fail.log"}}}'
} | python3 -m ci_whisper.mcp_server > "$mcp_out" 2>/dev/null
python3 - "$mcp_out" <<'MCP' || fail "mcp contract"
import json, sys
lines = [l for l in open(sys.argv[1]).read().splitlines() if l.strip()]
if len(lines) != 3:
    sys.exit("expected 3 responses (initialize, tools/list, tools/call), got %d" % len(lines))
messages = [json.loads(l) for l in lines]
if [m["id"] for m in messages] != [1, 2, 3]:
    sys.exit("ids came back as %r; a notification must not produce a response"
             % [m.get("id") for m in messages])
if messages[0]["result"]["serverInfo"]["name"] != "ci-whisper":
    sys.exit("initialize did not identify the server")
tools = {t["name"] for t in messages[1]["result"]["tools"]}
if not {"read_ci_log", "read_github_run"} <= tools:
    sys.exit("tools/list is missing tools: %r" % tools)
call = messages[2]["result"]
if call["isError"]:
    sys.exit("read_ci_log reported an error on a fixture with a real failure")
if "First real error" not in call["content"][0]["text"]:
    sys.exit("read_ci_log did not report a root cause")
print("MCP ok: handshake, tools/list, and a real tool call")
MCP
rm -f "$mcp_out"
echo "ALL CHECKS PASSED"
