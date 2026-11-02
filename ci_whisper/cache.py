"""Why a cache step missed, read from the lines actions/cache actually logs.

A cache miss is invisible until it makes a job twice as slow, and the log already says what
happened if you know the shapes: a key that was not found, a restore that came from a
different key than the one requested, a save that never happened, or an exact hit that will
never be refreshed because nothing new is ever saved.

Every verdict here cites the line it came from. Nothing is inferred about the server side,
because eviction cannot be seen from inside a job.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import List, Optional

NOT_FOUND = re.compile(r"cache not found for (?:input )?keys?:\s*(?P<keys>.+)$", re.I)
RESTORED = re.compile(r"cache restored from key:\s*(?P<key>\S+)", re.I)
HIT_PRIMARY = re.compile(r"cache hit occurred on the primary key\s+(?P<key>\S+?)[,\s]", re.I)
SAVED = re.compile(r"cache saved with (?:the )?key:\s*(?P<key>\S+)", re.I)
RESERVE = re.compile(r"unable to reserve cache with key\s+(?P<key>\S+)", re.I)
FAIL_ON_MISS = re.compile(r"fail-on-cache-miss", re.I)
RESTORE_FAILED = re.compile(r"cache (?:service|restore).*(?:fail|unavailable|503)", re.I)
GITLAB_CHECK = re.compile(r"checking cache for\s+(?P<key>\S+)", re.I)
GITLAB_EXTRACT = re.compile(r"successfully extracted cache", re.I)
GITLAB_NONE = re.compile(r"no url provided, cache will not be downloaded", re.I)


@dataclass
class CacheEvent:
    """One cache interaction found in the log."""

    action: str                     # restore | save | hit | miss | failed
    key: str
    line: int
    raw: str
    requested: Optional[List[str]] = None
    saved: bool = False

    def to_dict(self) -> dict:
        return {"action": self.action, "key": self.key, "line": self.line,
                "requested": self.requested, "saved": self.saved}


@dataclass
class Verdict:
    """What happened to one cache, and what to do about it."""

    status: str                     # hit | partial | cold | lost | stale-risk | failed
    key: str
    detail: str
    fix: str
    line: int
    severity: str = "info"          # info | low | medium | high

    def to_dict(self) -> dict:
        return {"status": self.status, "key": self.key, "detail": self.detail,
                "fix": self.fix, "line": self.line, "severity": self.severity}


@dataclass
class CacheReport:
    events: List[CacheEvent] = field(default_factory=list)
    verdicts: List[Verdict] = field(default_factory=list)

    #: Statuses that are not problems: a hit is the goal, and a cold cache is a first run
    #: or a genuine dependency change. Failing CI on those would train people to ignore it.
    NOT_PROBLEMS = frozenset({"hit", "cold"})

    @property
    def problems(self) -> List[Verdict]:
        """Verdicts worth failing a build over."""
        return [v for v in self.verdicts
                if v.status not in self.NOT_PROBLEMS and v.severity in {"medium", "high"}]

    @property
    def risks(self) -> List[Verdict]:
        """Worth saying, not worth failing: a low-severity trap someone may be fine with."""
        return [v for v in self.verdicts
                if v.status not in self.NOT_PROBLEMS and v.severity == "low"]

    def as_dict(self) -> dict:
        return {"events": [e.to_dict() for e in self.events],
                "verdicts": [v.to_dict() for v in self.verdicts],
                "problems": len(self.problems),
                "risks": len(self.risks)}


def _events(log: str) -> List[CacheEvent]:
    out: List[CacheEvent] = []
    for number, raw in enumerate(log.splitlines(), 1):
        line = raw.strip()
        if not line:
            continue
        if m := NOT_FOUND.search(line):
            keys = [k.strip() for k in m.group("keys").split(",") if k.strip()]
            out.append(CacheEvent("miss", keys[0] if keys else "", number, line, requested=keys))
        elif m := RESTORED.search(line):
            out.append(CacheEvent("restore", m.group("key"), number, line))
        elif m := HIT_PRIMARY.search(line):
            out.append(CacheEvent("hit", m.group("key"), number, line))
        elif m := SAVED.search(line):
            out.append(CacheEvent("save", m.group("key"), number, line, saved=True))
        elif m := RESERVE.search(line):
            out.append(CacheEvent("failed", m.group("key"), number, line))
        elif m := GITLAB_CHECK.search(line):
            out.append(CacheEvent("miss", m.group("key"), number, line))
        elif GITLAB_EXTRACT.search(line):
            out.append(CacheEvent("restore", "", number, line))
        elif GITLAB_NONE.search(line):
            out.append(CacheEvent("failed", "", number, line))
        elif FAIL_ON_MISS.search(line):
            out.append(CacheEvent("failed", "", number, line))
        elif RESTORE_FAILED.search(line):
            out.append(CacheEvent("failed", "", number, line))
    return out


def _verdicts(events: List[CacheEvent]) -> List[Verdict]:
    """Turn the events into one verdict per cache interaction, worst first."""
    out: List[Verdict] = []
    for event in events:
        if event.action == "hit":
            out.append(Verdict(
                "stale-risk", event.key, line=event.line, severity="low",
                detail="the primary key matched, so the cache was restored and not saved. "
                       "If this key has no content hash in it, the same stale cache is served "
                       "until it is evicted",
                fix="put a hash of the dependency file in the key (hashFiles('**/lockfile')) "
                    "so a changed dependency makes a new key"))
        elif event.action == "restore":
            requested = next((e.requested for e in events
                              if e.action == "miss" and e.requested), None)
            primary = requested[0] if requested else None
            if primary and event.key and event.key.strip() != primary.strip():
                out.append(Verdict(
                    "partial", event.key, line=event.line, severity="medium",
                    detail="the exact key (%s) did not exist and a restore-key prefix did. "
                           "Either the hashed file changed or that cache lives on another "
                           "branch: caches are scoped to the branch and its base" % primary,
                    fix="compare the restored key with the one requested; if the suffix is a "
                        "hash, the dependency file changed, which is expected. If it is the "
                        "same, the cache was built on another branch"))
            elif primary:
                out.append(Verdict(
                    "hit", event.key, line=event.line,
                    detail="restored the exact key that was requested",
                    fix="nothing to do"))
            else:
                out.append(Verdict("hit", event.key or "(unnamed)", line=event.line,
                                   detail="cache extracted", fix="nothing to do"))
        elif event.action == "miss":
            # A restore after the miss answers it; actions/cache logs the miss line even
            # when the exact key then restores, so speaking here reports a hit as a problem.
            if any(e.line > event.line and e.action in {"restore", "hit"} for e in events):
                continue
            out.append(Verdict(
                "cold", event.key, line=event.line, severity="low",
                detail="no cache matched this key, so the step recomputed everything",
                fix="expected on the first run or after a dependency change; if it repeats "
                    "with the same key, the save step is not running on this branch"))
        elif event.action == "failed":
            failed_hard = bool(FAIL_ON_MISS.search(event.raw))
            out.append(Verdict(
                "failed", event.key or "(unnamed)", line=event.line,
                severity="high" if failed_hard else "medium",
                detail=("the job failed because the cache was required and missing "
                        "(fail-on-cache-miss is set)"
                        if failed_hard else
                        "the cache could not be reserved or restored: another job may be "
                        "saving the same key, or the service was unavailable"),
                fix=("drop fail-on-cache-miss for a cache that is an optimisation, not a "
                     "requirement" if failed_hard else
                     "make the key unique per job (include the job name or matrix value) so "
                     "two jobs cannot race to save it")))

    # A miss with no save anywhere is the one that never recovers.
    saved_keys = {e.key for e in events if e.action == "save"}
    misses = [v for v in out if v.status == "cold"]
    if misses:
        for verdict in misses:
            if verdict.key in saved_keys:
                continue
            verdict.status = "lost"
            verdict.severity = "medium"
            verdict.detail = ("no cache matched and nothing was saved afterwards, so this job "
                              "will miss again next time")
            verdict.fix = ("check that the save step runs even when the job fails "
                           "(if: always()) and that the path it saves actually exists")

    order = {"high": 0, "medium": 1, "low": 2, "info": 3}
    out.sort(key=lambda v: (order[v.severity], v.line))
    return out


def analyze_cache(log: str) -> CacheReport:
    """Read a CI log and explain every cache interaction in it."""
    events = _events(log)
    return CacheReport(events=events, verdicts=_verdicts(events))


def render_cache(report: CacheReport) -> str:
    """Human-readable report, in the same voice as the rest of ci-whisper."""
    if not report.events:
        return ("No cache activity found in this log.\n"
                "Either the job has no cache step, or the log does not include the "
                "post-job steps where restores and saves are printed.")
    lines = []
    worst = next((v.severity for v in report.verdicts if v.severity != "info"), "info")
    lines.append("ci-whisper cache: %d cache interaction(s), worst %s"
                 % (len(report.verdicts), worst))
    lines.append("")
    for verdict in report.verdicts:
        lines.append("[%s] line %d: %s" % (verdict.severity.upper(), verdict.line, verdict.status))
        if verdict.key:
            lines.append("  key:    %s" % verdict.key)
        lines.append("  why:    %s" % verdict.detail)
        lines.append("  fix:    %s" % verdict.fix)
        lines.append("")
    return "\n".join(lines).rstrip()
