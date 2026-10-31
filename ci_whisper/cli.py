"""Command line entry point for ci-whisper."""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

from . import __version__
from .parse import Diagnosis, diagnose


def read_log(args: argparse.Namespace) -> str:
    """Get the log text from a file, stdin, or `gh run view`."""
    if args.file:
        # Decode without universal-newline translation: GitLab markers contain CR.
        return Path(args.file).read_bytes().decode("utf-8", errors="replace")
    if args.gh:
        result = subprocess.run(
            ["gh", "run", "view", args.gh, "--log-failed"],
            capture_output=True,
            check=False,
        )
        if result.returncode != 0 and not result.stdout:
            error = result.stderr.decode("utf-8", errors="replace").strip()
            print(f"ci-whisper: gh failed: {error}", file=sys.stderr)
            raise SystemExit(2)
        return result.stdout.decode("utf-8", errors="replace")
    if sys.stdin.isatty():
        print("ci-whisper: pass a log on stdin, --file, or --gh <run-id>", file=sys.stderr)
        raise SystemExit(2)
    if hasattr(sys.stdin, "buffer"):
        return sys.stdin.buffer.read().decode("utf-8", errors="replace")
    return sys.stdin.read()


def render(diagnosis: Diagnosis) -> str:
    """Human-readable report."""
    out: list[str] = []
    if diagnosis.root_cause is None:
        out.append("No error found in this log.")
        out.append(f"Scanned {diagnosis.scanned} lines, {diagnosis.signals} signals.")
        return "\n".join(out)

    root = diagnosis.root_cause
    out.append("FIRST REAL ERROR")
    out.append(f"  line {root.line_no}: {root.text}")
    out.append("")
    out.append("CAUSAL CHAIN")
    for hit in diagnosis.chain:
        role = "root" if hit is root else hit.kind
        out.append(f"  {role:12} line {hit.line_no}: {hit.text}")
    out.append("")
    out.append("SUGGESTED FIX")
    out.append(f"  {diagnosis.fix}")
    out.append("")
    out.append(f"Scanned {diagnosis.scanned} lines, {diagnosis.signals} signals.")
    return "\n".join(out)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="ci-whisper",
        description="Read a failed CI log and report the first real error, its chain, and a fix.",
    )
    parser.add_argument("--file", "-f", help="log file to read")
    parser.add_argument("--gh", metavar="RUN_ID", help="GitHub Actions run id (uses gh run view)")
    parser.add_argument("--json", action="store_true", help="machine-readable output")
    parser.add_argument("--provider", choices=("github", "gitlab", "auto"), default="auto",
                        help="CI log format (default: auto, detect GitLab section markers)")
    parser.add_argument("--version", action="version", version=f"ci-whisper {__version__}")
    args = parser.parse_args(argv)

    diagnosis = diagnose(read_log(args), provider=args.provider)
    if args.json:
        print(json.dumps(diagnosis.as_dict(), indent=2))
    else:
        print(render(diagnosis))
    return 0 if diagnosis.root_cause else 1


if __name__ == "__main__":
    sys.exit(main())
