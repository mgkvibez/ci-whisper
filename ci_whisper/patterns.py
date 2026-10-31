"""Signal and noise patterns for CI log classification."""

from __future__ import annotations

import re

ANSI_RE = re.compile(r"\x1b\[[0-9;]*[A-Za-z]")
TIMESTAMP_RE = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d+Z\s?")
GITLAB_SECTION_RE = re.compile(
    r"^section_(?:start|end):\d+:[A-Za-z0-9_.-]+(?:\[[^\]\r\n]*\])?(?:\r|$)"
)
JOB_PREFIX_RE = re.compile(r"^[^\t]*\t(?=[a-z-]+\t)")
GO_TEST_FAILURE_RE = re.compile(r"^--- FAIL: (\S+) \(\d+(?:\.\d+)?s\)$")
GO_PACKAGE_FAILURE_RE = re.compile(r"^FAIL\s+(\S+)\s+(?:\d+(?:\.\d+)?s|\[build failed\])(?:\s|$)")
GO_COMPILER_ERROR_RE = re.compile(r"^\S+\.go:\d+:\d+:\s+\S")

# Errors that are almost always the real cause.
STRONG_ERRORS: list[tuple[str, re.Pattern[str]]] = [
    ("python-traceback", re.compile(r"^Traceback \(most recent call last\):")),
    ("module-not-found", re.compile(r"^ModuleNotFoundError:|^ImportError:")),
    ("npm-error", re.compile(r"^npm ERR!")),
    ("github-error", re.compile(r"^##\[error\]")),
    ("compiler-error", re.compile(r"\berror(\[E\d+\])?:")),
    ("go-test-failure", GO_TEST_FAILURE_RE),
    ("test-failure", re.compile(r"^(FAILED|FAIL|✕|×)\b")),
    ("fatal", re.compile(r"^fatal:")),
    ("exception", re.compile(r"^[A-Za-z_.]+(Error|Exception):\s")),
    ("non-zero-exit", re.compile(r"process completed with exit code [1-9]")),
]

# Lines that look like errors but are noise.
NOISE: list[re.Pattern[str]] = [
    re.compile(r"\b\d+\s+(passed|failed)=?\s*0 failed", re.IGNORECASE),
    re.compile(r"^warning:?|^W\d{4}\b|\bWARN\b", re.IGNORECASE),
    re.compile(r"deprecat", re.IGNORECASE),
    re.compile(r"^\s*$"),
    re.compile(r"^\s*\d+%|\bDownloading\b|\bCollecting\b", re.IGNORECASE),
    re.compile(r"^npm WARN", re.IGNORECASE),
    re.compile(r"\b0 errors?\b", re.IGNORECASE),
]

# Markers that a previous phase ended; errors after these are the interesting ones.
PHASE_MARKERS: list[re.Pattern[str]] = [
    re.compile(r"^##\[group\](.*)$"),
    re.compile(r"^Run\s+(.+)$"),
]

CAUSAL_HINTS: list[re.Pattern[str]] = [
    re.compile(r"caused by:", re.IGNORECASE),
    re.compile(r"\bduring handling of the above exception", re.IGNORECASE),
    re.compile(r"\bdue to\b", re.IGNORECASE),
    re.compile(r"^The above exception was the direct cause", re.IGNORECASE),
    re.compile(r"^\s+npm ERR!.*\n?", re.IGNORECASE),
]

FIX_RULES: list[tuple[re.Pattern[str], str]] = [
    (re.compile(r"ModuleNotFoundError: No module named '([^']+)'"),
     "Install the missing module (pip install {0}) and add it to your dependencies so CI installs it."),
    (re.compile(r"npm ERR! code ERESOLVE"),
     "Peer dependency conflict: align the conflicting versions or use a compatible lockfile."),
    (re.compile(r"npm ERR!.*ETARGET|No matching version found for (\S+)"),
     "The requested version does not exist in the registry. Pin an existing version."),
    (re.compile(r"Permission denied \(publickey\)|Host key verification failed"),
     "The deploy key or known_hosts entry is missing or rotated. Check repository secrets."),
    (re.compile(r"No such file or directory: '([^']+)'"),
     "A required file is missing at {0}. Verify the checkout path and that the file is committed."),
    (re.compile(r"EADDRINUSE|address already in use", re.IGNORECASE),
     "Port conflict: the service is already bound. Kill the stale process or choose a free port."),
    (re.compile(r"AssertionError"),
     "An assertion failed: the actual value differs from the expected one. Read the assertion line for both values."),
    (re.compile(r"SyntaxError"),
     "The file does not parse. Fix the syntax before investigating anything downstream."),
    (re.compile(r"Timeout|timed out", re.IGNORECASE),
     "The step exceeded its time budget. Look for a hang or an unbounded network call."),
    (re.compile(r"Killed|signal: killed|OutOfMemory", re.IGNORECASE),
     "The process was killed, usually out of memory. Reduce memory use or raise the runner limit."),
]
