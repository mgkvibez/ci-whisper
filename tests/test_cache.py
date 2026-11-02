"""Cache diagnosis, driven by the log shapes actions/cache and GitLab really print."""

from ci_whisper.cache import analyze_cache, render_cache

PARTIAL = """Run actions/cache@v4
Cache not found for input keys: Linux-node-9f8c1a, Linux-node-
Received 200000000 of 800000000 (25.0%), 40.1 MBs/sec
Cache restored from key: Linux-node-77aa0b
Cache saved with key: Linux-node-9f8c1a
"""

EXACT = """Cache not found for input keys: Linux-node-77aa0b
Cache restored from key: Linux-node-77aa0b
"""

COLD = """Cache not found for input keys: Linux-node-9f8c1a
Cache saved with key: Linux-node-9f8c1a
"""

LOST = "Cache not found for input keys: Linux-node-9f8c1a\n"

POISON = "Cache hit occurred on the primary key Linux-node, not saving cache.\n"

HARD_FAIL = """Cache not found for input keys: Linux-node-x
Warning: Failed to restore cache entry. Exiting as fail-on-cache-miss is set.
"""

RACE = ("Failed to save: Unable to reserve cache with key Linux-node-x, "
        "another job may be creating this cache.\n")


def _statuses(target):
    """Accept either a log or an already-analysed report."""
    report = target if hasattr(target, "verdicts") else analyze_cache(target)
    return [v.status for v in report.verdicts]


def test_a_prefix_restore_is_partial_and_names_both_keys():
    report = analyze_cache(PARTIAL)
    assert "partial" in _statuses(report)
    partial = next(v for v in report.verdicts if v.status == "partial")
    assert "9f8c1a" in partial.detail and "77aa0b" in partial.key


def test_the_miss_behind_a_partial_restore_is_not_reported_twice():
    """Both lines describe one cache interaction; two findings is noise."""
    assert _statuses(PARTIAL).count("cold") == 0


def test_the_exact_key_is_a_hit_and_clean():
    report = analyze_cache(EXACT)
    assert _statuses(report) == ["hit"]
    assert report.problems == []


def test_a_miss_that_saves_is_cold_not_lost():
    report = analyze_cache(COLD)
    assert _statuses(report) == ["cold"]
    assert report.problems == []          # a first run is expected, not a problem


def test_a_miss_with_no_save_is_lost_and_actionable():
    report = analyze_cache(LOST)
    assert _statuses(report) == ["lost"]
    assert report.verdicts[0].severity == "medium"
    assert "always()" in report.verdicts[0].fix


def test_an_exact_hit_that_never_saves_is_a_stale_risk():
    report = analyze_cache(POISON)
    assert _statuses(report) == ["stale-risk"]
    assert "hashFiles" in report.verdicts[0].fix


def test_fail_on_cache_miss_is_the_high_severity_case():
    report = analyze_cache(HARD_FAIL)
    assert report.verdicts[0].severity == "high"
    assert "fail-on-cache-miss" in report.verdicts[0].detail


def test_a_lost_race_to_save_is_medium_and_names_the_race():
    report = analyze_cache(RACE)
    assert report.verdicts[0].severity == "medium"
    assert "race" in report.verdicts[0].fix


def test_gitlab_cache_lines_are_recognised():
    log = "Checking cache for linux-node...\nNo URL provided, cache will not be downloaded.\n"
    assert analyze_cache(log).events


def test_a_log_without_cache_steps_says_so_rather_than_guessing():
    assert "No cache activity found" in render_cache(analyze_cache("Step 1: npm ci\nDone.\n"))


def test_line_numbers_point_at_the_evidence():
    report = analyze_cache(PARTIAL)
    partial = next(v for v in report.verdicts if v.status == "partial")
    assert PARTIAL.splitlines()[partial.line - 1].startswith("Cache restored from key")
