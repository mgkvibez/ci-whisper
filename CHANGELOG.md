# Changelog

## v0.3.0 - 2026-11-02

- New mode: `ci-whisper cache --file <log>` explains what happened to every cache step in a
  run, from the log alone. Six verdicts: `hit`, `cold`, `partial`, `lost`, `stale-risk`,
  `failed`.
- `partial` is the one people cannot see themselves: the exact key was missing and a
  restore-key prefix was served instead, so the job silently rebuilt against stale
  dependencies.
- `lost` means nothing matched and nothing was saved, so it will miss again next run, and the
  fix names the usual cause (`if: always()` missing on the save step).
- `stale-risk` flags an exact-key hit that never saves, where an unhashed key serves the same
  cache until eviction.
- Four bugs found by its own fixtures before release: an exact-key restore logged after a
  "not found" line was reported as `lost` (a hit reported as a problem); a miss answered by a
  restore was described twice; a cold cache counted as a problem, so CI would fail on a first
  run; and one save cleared every miss instead of only its own key.
- Exit codes: 0 when nothing is wrong (including a cold first run), 1 when a cache problem is
  real, 2 when the log cannot be read.

## v0.2.0 - 2026-10-31

ci-whisper is now an MCP server as well as a CLI, so an agent can read a failed run
instead of a human pasting a log into it.

- New `ci-whisper-mcp` console script speaking JSON-RPC 2.0 over stdio: `initialize`,
  `ping`, `tools/list`, `tools/call`, plus notifications. Implemented without the `mcp`
  package, because a package whose selling point is zero runtime dependencies should not
  acquire one to expose its own output.
- Two tools: `read_ci_log` (log text or path, any supported provider) and
  `read_github_run` (fetches with `gh run view --log-failed`, then analyses). Tool results
  carry `structuredContent` alongside text, so an agent can branch on the root cause
  without parsing prose.
- stdout is protocol-only; diagnostics go to stderr. A stray print would corrupt the
  stream, and a malformed request line is answered with a `-32700` and the session
  continues rather than dying.
- No token, no writes, no PRs. The server holds no credentials and cannot touch a repo.
- `scripts/verify.sh` gained check 5, which drives a real handshake and tool call through
  stdio, and its numbering was corrected (check 1 said "1/3" while the rest said "n/4").

## v0.1.3 - 2026-10-31

- GitLab CI support: `--provider {auto,github,gitlab}`, default `auto`. GitLab section
  markers are recognised, and logs are read without universal-newline translation, so
  the line a diagnosis cites is the line in the real file. On an 11-line job log this
  previously reported line 12, because `splitlines()` counted the `\r` inside a section
  marker as a line break. Contributed by @Sonike in #4.
- GitHub behaviour is unchanged: `auto` keeps the existing cleaning path whenever no
  GitLab markers are present.

## v0.1.2 - 2026-10-29

- Fix the reported version: `--version` said 0.1.0 while the package was
  0.1.1, because the module constant and the package metadata were bumped
  separately. The release gate now fails when they disagree.
## v0.1.1 - 2026-10-29

- First PyPI release (`pipx install ci-whisper`).
- Go support: `--- FAIL: TestX` is now a first-class signal, an earlier compiler
  error still outranks a later test failure, and the failing package is recovered
  from the trailing summary. Contributed by @Sonike in #3.
- Release pipeline via PyPI trusted publishing (no stored token), which refuses to
  publish when the tag and pyproject version disagree.
- First-time contributor workflow runs are auto-approved, except PRs that touch CI.
## v0.1.0 - 2026-10-29

- Read a failed CI log from stdin, `--file`, or `--gh <run-id>` and report the
  first real error, its causal chain, and a suggested fix, citing original log
  line numbers instead of line numbers in a cleaned copy.
- Signal ranking so consequences do not masquerade as causes: exceptions and
  module errors (4) outrank the bare `Traceback` header, test and compiler
  failures (3), then npm and GitHub annotations (2), then a lone exit code (1).
- Go test failures recognized at strength 3 with package-name recovery, and an
  earlier compiler diagnostic keeps precedence over a later test failure.
  Contributed by @Sonike in #3.
- `--json` for machine consumption; exit 1 when no error signal is found at all.
- 13 tests over real log shapes (pytest, npm, traceback, Go).
- Install: `pipx install git+https://github.com/mgkvibez/ci-whisper.git`.
