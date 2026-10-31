"""Turn a raw CI log into a diagnosis."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Iterable

from .patterns import (
    ANSI_RE,
    CAUSAL_HINTS,
    FIX_RULES,
    GITLAB_SECTION_RE,
    GO_COMPILER_ERROR_RE,
    GO_PACKAGE_FAILURE_RE,
    GO_TEST_FAILURE_RE,
    NOISE,
    STRONG_ERRORS,
    TIMESTAMP_RE,
)


@dataclass
class Hit:
    """One signal found in the log."""

    line_no: int
    text: str
    kind: str
    strength: int


@dataclass
class Diagnosis:
    """Result of reading a log."""

    root_cause: Hit | None
    chain: list[Hit] = field(default_factory=list)
    fix: str = ""
    phase: str = ""
    scanned: int = 0
    signals: int = 0

    def as_dict(self) -> dict[str, object]:
        return {
            "phase": self.phase,
            "scanned_lines": self.scanned,
            "signals": self.signals,
            "root_cause": (
                {
                    "line": self.root_cause.line_no,
                    "kind": self.root_cause.kind,
                    "text": self.root_cause.text,
                }
                if self.root_cause
                else None
            ),
            "chain": [
                {"line": h.line_no, "kind": h.kind, "text": h.text} for h in self.chain
            ],
            "suggested_fix": self.fix,
        }


def clean_lines(raw: str, provider: str = "auto") -> list[str]:
    """Clean provider metadata, keeping one entry per original newline."""
    if provider not in {"auto", "github", "gitlab"}:
        raise ValueError(f"Unknown provider: {provider}")
    # GitLab embeds a carriage return between a section marker and its header.
    # splitlines() would count that as a second line and shift every citation.
    lines = raw.split("\n") if raw else []
    if raw.endswith("\n"):
        lines.pop()
    lines = [TIMESTAMP_RE.sub("", ANSI_RE.sub("", line.rstrip("\r"))) for line in lines]
    if provider == "auto":
        provider = "gitlab" if any(GITLAB_SECTION_RE.match(line) for line in lines) else "github"
    if provider == "gitlab":
        # Keep the displayed header, or an empty placeholder for marker-only lines.
        lines = [GITLAB_SECTION_RE.sub("", line) for line in lines]
    return lines


def is_noise(line: str) -> bool:
    """True when a line looks like an error but is not worth reporting."""
    return any(pattern.search(line) for pattern in NOISE)


def classify(line: str) -> tuple[str, int] | None:
    """Return (kind, strength) for a matching error line, else None."""
    text = line.strip()
    if not text or is_noise(text):
        return None
    # Go compiler diagnostics have no "error:" prefix. Keep indentation here:
    # a test's indented source location is context, not a compiler error.
    if GO_COMPILER_ERROR_RE.search(line):
        return "compiler-error", 3
    for kind, pattern in STRONG_ERRORS:
        if pattern.search(text):
            # The exception line is the cause; the Traceback header is only its preface.
            if kind in {"module-not-found", "exception"}:
                strength = 4
            elif kind in {"python-traceback", "test-failure", "go-test-failure", "compiler-error"}:
                strength = 3
            elif kind in {"npm-error", "github-error"}:
                strength = 2
            else:
                strength = 1
            return kind, strength
    return None


def find_signals(lines: Iterable[str]) -> list[Hit]:
    """Collect every non-noise error signal with its original line number."""
    hits: list[Hit] = []
    for index, line in enumerate(lines, start=1):
        found = classify(line)
        if found:
            kind, strength = found
            hits.append(Hit(line_no=index, text=line.strip(), kind=kind, strength=strength))
    return hits


def find_phase(lines: list[str]) -> str:
    """Name the step that was running when the first signal appeared."""
    current = ""
    for line in lines:
        stripped = line.strip()
        if stripped.startswith("##[group]"):
            current = stripped[len("##[group]") :].strip() or current
        elif stripped.startswith("##[endgroup]"):
            continue
    return current


def pick_root_cause(hits: list[Hit]) -> Hit | None:
    """Choose the earliest strong signal; prefer structured errors over exit codes."""
    if not hits:
        return None
    structured = [h for h in hits if h.strength >= 3]
    if structured:
        # Highest severity first, then earliest line: the exception beats its own preface.
        return sorted(structured, key=lambda h: (-h.strength, h.line_no))[0]
    non_exit = [h for h in hits if h.kind != "non-zero-exit"]
    return (non_exit or hits)[0]


def build_chain(root: Hit | None, hits: list[Hit], lines: list[str]) -> list[Hit]:
    """Collect related signals: the root cause, its context, and later consequences."""
    if root is None:
        return []
    chain: list[Hit] = [root]
    downstream = [h for h in hits if h.line_no > root.line_no][:3]
    chain.extend(downstream)
    for offset in range(root.line_no, min(root.line_no + 12, len(lines) + 1)):
        text = lines[offset - 1].strip()
        if offset == root.line_no:
            continue
        if any(hint.search(text) for hint in CAUSAL_HINTS) and not is_noise(text):
            chain.append(Hit(line_no=offset, text=text, kind="causal", strength=1))
            break
    seen: set[int] = set()
    unique: list[Hit] = []
    for hit in chain:
        if hit.line_no not in seen:
            seen.add(hit.line_no)
            unique.append(hit)
    return unique


TEST_ID_RE = re.compile(r"^(?:FAILED\s+)?(?P<test>[\w./-]+\.py::[\w:.\[\]-]+)")


ASSERTION_LINE = re.compile(
    r"^\s*(?:E\s+)?(?P<msg>(?:[A-Za-z_][\w.]*(?:Error|Exception)|AssertionError)[: ].*?)\s*$")


def _failure_detail(root: "Hit", lines: list[str] | None, test_id: str) -> str:
    """The message after a FAILED line, or "" when the log does not carry one.

    Returning the raw line as a "detail" is worse than returning nothing: it
    produces sentences like "test_y failed with FAILED test_y". Better to say
    the log had no message than to echo it.
    """
    if " - " in root.text:
        detail = root.text.split(" - ", 1)[1].strip()
        if detail and detail != test_id:
            return detail
    # line_no is 1-based, so slicing from it lands on the following line; the
    # explicit skip keeps this correct if a caller ever hands over 0-based.
    for line in (lines or [])[root.line_no:]:
        stripped = line.strip()
        if stripped == root.text.strip():
            continue
        if stripped.startswith("##[") or stripped.startswith("Run "):
            break
        match = ASSERTION_LINE.match(line)
        if match:
            return match.group("msg").strip()
    return ""


def suggest_fix(root: Hit | None, lines: list[str] | None = None) -> str:
    """Map the root cause to a concrete first action."""
    if root is None:
        return "No error signal found. The log may be truncated or the failure is a cancelled step."
    if root.kind == "go-test-failure":
        match = GO_TEST_FAILURE_RE.search(root.text)
        if match:
            package = ""
            for line in (lines or [])[root.line_no:]:
                text = line.strip()
                if text.startswith(("##[group]", "##[endgroup]", "Run ")):
                    break
                result = GO_PACKAGE_FAILURE_RE.search(text)
                if result:
                    package = f" in package {result.group(1)}"
                    break
            return (
                f"Go test {match.group(1)}{package} failed. Re-run it alone and inspect "
                "its assertion output, then fix the expectation or the code it exercises."
            )
    if root.kind == "test-failure":
        match = TEST_ID_RE.search(root.text)
        if match:
            test = match.group("test")
            detail = _failure_detail(root, lines, test)
            if detail:
                return (
                    f"{test} failed with {detail}. Run it alone to reproduce, "
                    "then fix the expectation or the code it exercises."
                )
            return (
                f"{test} failed, but the log carries no message for it. Run it alone "
                "to reproduce, then fix the expectation or the code it exercises."
            )
    for pattern, template in FIX_RULES:
        match = pattern.search(root.text)
        if match:
            groups = [g for g in match.groups() if g]
            return template.format(*groups) if groups else template
    return (
        f"Read {root.text!r} at line {root.line_no}. The first real error is usually "
        "the one before the cascade of non-zero exits."
    )


def diagnose(raw: str, provider: str = "auto") -> Diagnosis:
    """Full pipeline: clean, find signals, pick root cause, chain, fix."""
    lines = clean_lines(raw, provider=provider)
    hits = find_signals(lines)
    root = pick_root_cause(hits)
    return Diagnosis(
        root_cause=root,
        chain=build_chain(root, hits, lines),
        fix=suggest_fix(root, lines),
        phase=find_phase(lines),
        scanned=len(lines),
        signals=len(hits),
    )
