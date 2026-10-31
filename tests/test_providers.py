"""Provider cleaning and CLI coverage, including raw GitLab carriage returns."""

import json
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from ci_whisper.parse import clean_lines, diagnose
from ci_whisper.cli import read_log

FIXTURES = Path(__file__).parent / "fixtures"


def gitlab_log():
    return (FIXTURES / "gitlab_fail.log").read_bytes().decode("utf-8")


@pytest.mark.parametrize("provider", ["auto", "gitlab"])
@pytest.mark.parametrize("crlf", [False, True])
def test_gitlab_preserves_original_line_numbers(provider, crlf):
    raw = gitlab_log()
    if crlf:
        raw = raw.replace("\n", "\r\n")
    lines = clean_lines(raw, provider=provider)
    assert len(lines) == 11
    assert lines[1] == "Preparing the executor"
    assert lines[3] == ""
    assert lines[4] == "Executing the job script"
    assert not any("section_" in line or "\x1b" in line or "\r" in line for line in lines)
    result = diagnose(raw, provider=provider)
    assert result.scanned == 11
    assert result.root_cause.line_no == 9
    assert result.root_cause.kind == "module-not-found"
    assert "pip install yaml" in result.fix


@pytest.mark.parametrize("name", ["pytest_fail.log", "npm_fail.log", "traceback.log", "go_fail.log", "noise_only.log"])
def test_auto_keeps_github_diagnoses(name):
    raw = (FIXTURES / name).read_text()
    assert diagnose(raw).as_dict() == diagnose(raw, provider="github").as_dict()


def test_plain_and_timestamped_gitlab_markers():
    raw = (
        "2026-10-30T02:00:00.000Z section_start:1698567123:build\n"
        "ModuleNotFoundError: No module named 'yaml'\n"
        "section_end:1698567124:build\n"
    )
    assert clean_lines(raw) == ["", "ModuleNotFoundError: No module named 'yaml'", ""]
    assert diagnose(raw).root_cause.line_no == 2
    assert "section_start" in clean_lines(raw, provider="github")[0]


def test_marker_mentions_are_not_removed():
    raw = "printing section_start:1698567123:build\nsection_start:invalid:build\n"
    assert clean_lines(raw, provider="gitlab") == raw.splitlines()


@pytest.mark.parametrize("raw, expected", [("", []), ("\n", [""]), ("hello\r\n\r\n", ["hello", ""]), ("hello", ["hello"])])
def test_empty_and_trailing_lines(raw, expected):
    assert clean_lines(raw) == expected


def test_unknown_provider_is_rejected():
    with pytest.raises(ValueError, match="provider"):
        diagnose("", provider="unknown")


@pytest.mark.parametrize("provider", [None, "auto", "gitlab"])
@pytest.mark.parametrize("source", ["file", "stdin"])
def test_cli_reads_raw_gitlab_without_expanding_carriage_returns(provider, source):
    args = [sys.executable, "-m", "ci_whisper.cli", "--json"]
    if provider:
        args += ["--provider", provider]
    if source == "file":
        args += ["--file", str(FIXTURES / "gitlab_fail.log")]
    result = subprocess.run(args, input=gitlab_log().encode() if source == "stdin" else None,
                            stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    assert result.returncode == 0, result.stderr
    report = json.loads(result.stdout)
    assert report["root_cause"]["line"] == 9
    assert report["scanned_lines"] == 11
    assert "pip install yaml" in report["suggested_fix"]


def test_cli_rejects_unknown_provider():
    result = subprocess.run([sys.executable, "-m", "ci_whisper.cli", "--provider", "unknown"],
                            stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    assert result.returncode == 2
    assert b"invalid choice" in result.stderr


def test_gh_output_keeps_carriage_returns(monkeypatch):
    def run(command, **kwargs):
        assert command == ["gh", "run", "view", "123", "--log-failed"]
        assert not kwargs.get("text")
        return SimpleNamespace(returncode=0, stdout=gitlab_log().encode(), stderr=b"")

    monkeypatch.setattr(subprocess, "run", run)
    assert read_log(SimpleNamespace(file=None, gh="123")) == gitlab_log()


def test_gh_errors_are_decoded(monkeypatch, capsys):
    monkeypatch.setattr(subprocess, "run", lambda *args, **kwargs:
                        SimpleNamespace(returncode=1, stdout=b"", stderr=b"not logged in\n"))
    with pytest.raises(SystemExit) as exc:
        read_log(SimpleNamespace(file=None, gh="123"))
    assert exc.value.code == 2
    assert capsys.readouterr().err == "ci-whisper: gh failed: not logged in\n"
