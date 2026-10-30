"""Suggestion text must never degrade into echoing the failure line back."""
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from ci_whisper.parse import diagnose


def test_detail_after_separator_is_used():
    log = "FAILED tests/test_x.py::test_y - AssertionError: 1 != 2\n1 failed in 0.1s\n"
    fix = diagnose(log).fix
    assert "tests/test_x.py::test_y" in fix
    assert "AssertionError: 1 != 2" in fix


def test_pytest_e_line_supplies_the_missing_detail():
    log = (
        "FAILED tests/test_x.py::test_y\n"
        "E   AssertionError: expected 3, got 4\n"
        "1 failed in 0.1s\n"
    )
    fix = diagnose(log).fix
    assert "expected 3, got 4" in fix
    assert "failed with FAILED" not in fix


def test_no_message_is_stated_rather_than_faked():
    log = "FAILED tests/test_x.py::test_y\n1 failed in 0.1s\n"
    fix = diagnose(log).fix
    assert "tests/test_x.py::test_y" in fix
    assert "failed with FAILED" not in fix
    assert "no message" in fix
