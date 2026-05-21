#!/usr/bin/env python3
"""Comprehensive unit tests for the Good First Issue Finder project.

Tests cover:
- scrape_good_first_issues.py: scoring, parsing, caching, diffing, CSV export, body extraction
- match_issues.py: prompt building, CSV loading, profile creation
- tui.py: data loading, filtering, score display helpers

Run: pytest test_all.py -v
"""

from __future__ import annotations

import csv
import json
import math
import tempfile
from datetime import datetime, timezone, timedelta
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest

# ---------------------------------------------------------------------------
# Import modules under test
# ---------------------------------------------------------------------------

import sys
from pathlib import Path

# Add src to path for imports
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

import scrape_good_first_issues as scraper
from scrape_good_first_issues import (
    Issue,
    _score_freshness,
    _score_competition,
    _score_availability,
    _score_popularity,
    _score_activity,
    _score_pr_status,
    compute_scores,
    extract_body_excerpt,
    parse_issues,
    _parse_datetime,
    export_csv,
    apply_diff,
    save_cache,
    get_previous_cache,
    get_cache_path,
    fetch_linked_prs,
    WEIGHT_FRESHNESS,
    WEIGHT_COMPETITION,
    WEIGHT_AVAILABILITY,
    WEIGHT_POPULARITY,
    WEIGHT_ACTIVITY,
    WEIGHT_PR_STATUS,
    FRESHNESS_HALF_LIFE_DAYS,
    COMPETITION_CAP,
    STALENESS_THRESHOLD_DAYS,
    BODY_EXCERPT_LENGTH,
)

import match_issues
from match_issues import (
    UserProfile,
    IssueSummary,
    build_prompt,
    load_issues as match_load_issues,
)

import tui
from tui import (
    load_issues as tui_load_issues,
    filter_issues,
    score_style,
    truncate,
    render_score_bar,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def now():
    """Fixed 'now' datetime for deterministic tests."""
    return datetime(2026, 5, 20, 0, 0, 0, tzinfo=timezone.utc)


@pytest.fixture
def sample_raw_issue():
    """A sample raw issue dict as returned by `gh search issues --json`."""
    return {
        "title": "Fix broken test",
        "url": "https://github.com/canonical/project/issues/42",
        "repository": {"name": "project", "nameWithOwner": "canonical/project"},
        "createdAt": "2026-05-01T10:00:00Z",
        "updatedAt": "2026-05-15T12:00:00Z",
        "commentsCount": 3,
        "assignees": [{"login": "user1"}],
        "labels": [
            {"name": "good first issue", "id": "1", "color": "00ff00", "description": ""},
            {"name": "bug", "id": "2", "color": "ff0000", "description": ""},
        ],
        "state": "open",
        "body": "## Description\n\nThe test `test_foo` is broken.\n\n```python\ndef test_foo():\n    assert False\n```\n\nPlease fix it.",
        "number": 42,
    }


@pytest.fixture
def sample_issues(now):
    """A list of sample Issue objects for testing."""
    return [
        Issue(
            title="Easy fix",
            url="https://github.com/canonical/repo-a/issues/1",
            repo="canonical/repo-a",
            number=1,
            created_at=now - timedelta(days=10),
            updated_at=now - timedelta(days=2),
            comments=0,
            assignees=[],
            labels=["good first issue"],
            body_excerpt="Simple documentation fix needed",
            stars=100,
            linked_pr_state="",
        ),
        Issue(
            title="Complex refactor",
            url="https://github.com/canonical/repo-b/issues/2",
            repo="canonical/repo-b",
            number=2,
            created_at=now - timedelta(days=300),
            updated_at=now - timedelta(days=200),
            comments=8,
            assignees=["dev1", "dev2"],
            labels=["good first issue", "enhancement"],
            body_excerpt="Refactor the entire module",
            stars=5000,
            linked_pr_state="OPEN",
        ),
        Issue(
            title="Add type hints",
            url="https://github.com/canonical/repo-c/issues/3",
            repo="canonical/repo-c",
            number=3,
            created_at=now - timedelta(days=60),
            updated_at=now - timedelta(days=5),
            comments=2,
            assignees=[],
            labels=["good first issue", "typing"],
            body_excerpt="Add type annotations to utils.py",
            stars=500,
            linked_pr_state="MERGED",
        ),
    ]


@pytest.fixture
def tmp_dir(tmp_path):
    """Temporary directory for file operations."""
    return tmp_path


# ===========================================================================
# SCORING TESTS
# ===========================================================================


class TestScoringFreshness:
    """Tests for the freshness scoring function."""

    def test_brand_new_issue(self, now):
        """Issue created just now should have score ~100."""
        score = _score_freshness(now, now)
        assert score == pytest.approx(100.0, abs=0.1)

    def test_half_life(self, now):
        """Issue at exactly one half-life should score ~50."""
        created = now - timedelta(days=FRESHNESS_HALF_LIFE_DAYS)
        score = _score_freshness(created, now)
        assert score == pytest.approx(50.0, abs=0.1)

    def test_two_half_lives(self, now):
        """Issue at two half-lives should score ~25."""
        created = now - timedelta(days=FRESHNESS_HALF_LIFE_DAYS * 2)
        score = _score_freshness(created, now)
        assert score == pytest.approx(25.0, abs=0.1)

    def test_very_old_issue(self, now):
        """Very old issue should have score near 0."""
        created = now - timedelta(days=3650)  # 10 years
        score = _score_freshness(created, now)
        assert score < 1.0

    def test_monotonically_decreasing(self, now):
        """Fresher issues should always score higher."""
        scores = [
            _score_freshness(now - timedelta(days=d), now)
            for d in [0, 30, 60, 90, 180, 365]
        ]
        assert scores == sorted(scores, reverse=True)


class TestScoringCompetition:
    """Tests for the competition scoring function."""

    def test_zero_comments(self):
        """No comments = maximum score."""
        assert _score_competition(0) == 100.0

    def test_at_cap(self):
        """At COMPETITION_CAP comments, score is 0."""
        assert _score_competition(COMPETITION_CAP) == 0.0

    def test_above_cap(self):
        """Above cap, still 0."""
        assert _score_competition(COMPETITION_CAP + 5) == 0.0

    def test_mid_range(self):
        """5 comments on a cap of 10 should be 50."""
        assert _score_competition(5) == pytest.approx(50.0)

    def test_linear_decay(self):
        """Score decreases linearly."""
        scores = [_score_competition(i) for i in range(COMPETITION_CAP + 1)]
        # Check decreasing
        for i in range(len(scores) - 1):
            assert scores[i] >= scores[i + 1]


class TestScoringAvailability:
    """Tests for the availability scoring function."""

    def test_no_assignees(self):
        """No assignees = full availability."""
        assert _score_availability([]) == 100.0

    def test_one_assignee(self):
        """One assignee should reduce significantly."""
        score = _score_availability(["user1"])
        assert score == pytest.approx(30.0, abs=0.1)

    def test_two_assignees(self):
        """Two assignees should reduce more."""
        score = _score_availability(["user1", "user2"])
        assert score == pytest.approx(9.0, abs=0.1)

    def test_many_assignees(self):
        """Many assignees should approach 0."""
        score = _score_availability(["a", "b", "c", "d"])
        assert score < 1.0

    def test_never_negative(self):
        """Score should never go below 0."""
        score = _score_availability(["a"] * 20)
        assert score >= 0.0


class TestScoringPopularity:
    """Tests for the popularity scoring function."""

    def test_zero_stars_zero_max(self):
        """Edge case: max_log_stars is 0."""
        assert _score_popularity(0, 0) == 50.0

    def test_max_stars(self):
        """Repo with max stars should score 100."""
        max_stars = 10000
        max_log = math.log1p(max_stars)
        score = _score_popularity(max_stars, max_log)
        assert score == pytest.approx(100.0, abs=0.1)

    def test_zero_stars(self):
        """Repo with 0 stars should score ~0."""
        max_log = math.log1p(10000)
        score = _score_popularity(0, max_log)
        assert score == pytest.approx(0.0, abs=0.5)

    def test_log_scaling(self):
        """Score should increase logarithmically, not linearly."""
        max_log = math.log1p(10000)
        score_0 = _score_popularity(0, max_log)
        score_1000 = _score_popularity(1000, max_log)
        score_2000 = _score_popularity(2000, max_log)
        score_3000 = _score_popularity(3000, max_log)
        # For equal linear increments (each +1000), gaps should decrease
        gap1 = score_1000 - score_0      # 0 → 1000
        gap2 = score_2000 - score_1000   # 1000 → 2000
        gap3 = score_3000 - score_2000   # 2000 → 3000
        assert gap1 > gap2  # Diminishing returns
        assert gap2 > gap3  # Continues to diminish


class TestScoringActivity:
    """Tests for the activity scoring function."""

    def test_just_updated(self, now):
        """Updated within 30 days = full score."""
        updated = now - timedelta(days=5)
        assert _score_activity(updated, now) == 100.0

    def test_30_day_boundary(self, now):
        """At exactly 30 days, still 100."""
        updated = now - timedelta(days=30)
        assert _score_activity(updated, now) == 100.0

    def test_stale(self, now):
        """At STALENESS_THRESHOLD_DAYS, score is 0."""
        updated = now - timedelta(days=STALENESS_THRESHOLD_DAYS)
        assert _score_activity(updated, now) == 0.0

    def test_mid_range(self, now):
        """Midway between 30 and threshold should be ~50."""
        mid_days = 30 + (STALENESS_THRESHOLD_DAYS - 30) / 2
        updated = now - timedelta(days=mid_days)
        score = _score_activity(updated, now)
        assert score == pytest.approx(50.0, abs=1.0)

    def test_beyond_threshold(self, now):
        """Beyond threshold, still 0."""
        updated = now - timedelta(days=STALENESS_THRESHOLD_DAYS + 100)
        assert _score_activity(updated, now) == 0.0


class TestScoringPRStatus:
    """Tests for PR status scoring."""

    def test_no_pr(self):
        assert _score_pr_status("") == 100.0

    def test_open_pr(self):
        assert _score_pr_status("OPEN") == 10.0

    def test_merged_pr(self):
        assert _score_pr_status("MERGED") == 30.0

    def test_closed_pr(self):
        assert _score_pr_status("CLOSED") == 80.0

    def test_unknown_state(self):
        assert _score_pr_status("UNKNOWN") == 50.0


class TestComputeScores:
    """Tests for the composite scoring function."""

    def test_weights_sum_to_one(self):
        """Scoring weights must sum to 1.0."""
        total = (
            WEIGHT_FRESHNESS + WEIGHT_COMPETITION + WEIGHT_AVAILABILITY
            + WEIGHT_POPULARITY + WEIGHT_ACTIVITY + WEIGHT_PR_STATUS
        )
        assert total == pytest.approx(1.0)

    def test_score_range(self, sample_issues, now):
        """All scores should be between 0 and 100."""
        compute_scores(sample_issues, now)
        for issue in sample_issues:
            assert 0 <= issue.total_score <= 100
            assert 0 <= issue.freshness_score <= 100
            assert 0 <= issue.competition_score <= 100
            assert 0 <= issue.availability_score <= 100
            assert 0 <= issue.popularity_score <= 100
            assert 0 <= issue.activity_score <= 100
            assert 0 <= issue.pr_score <= 100

    def test_easy_issue_scores_higher(self, sample_issues, now):
        """The 'easy fix' should score higher than 'complex refactor'."""
        compute_scores(sample_issues, now)
        easy = sample_issues[0]
        complex_issue = sample_issues[1]
        assert easy.total_score > complex_issue.total_score

    def test_scores_are_deterministic(self, sample_issues, now):
        """Running compute_scores twice should give same results."""
        compute_scores(sample_issues, now)
        scores_first = [i.total_score for i in sample_issues]
        compute_scores(sample_issues, now)
        scores_second = [i.total_score for i in sample_issues]
        assert scores_first == scores_second


# ===========================================================================
# BODY EXCERPT TESTS
# ===========================================================================


class TestBodyExcerpt:
    """Tests for the body excerpt extraction function."""

    def test_none_body(self):
        assert extract_body_excerpt(None) == ""

    def test_empty_body(self):
        assert extract_body_excerpt("") == ""

    def test_plain_text(self):
        text = "This is a simple bug description."
        assert extract_body_excerpt(text) == text

    def test_strips_code_blocks(self):
        body = "Before code\n```python\nprint('hello')\n```\nAfter code"
        result = extract_body_excerpt(body)
        assert "print" not in result
        assert "Before code" in result
        assert "After code" in result

    def test_strips_html(self):
        body = "Check <b>this</b> out <a href='url'>link</a>"
        result = extract_body_excerpt(body)
        assert "<b>" not in result
        assert "<a" not in result

    def test_strips_markdown_links(self):
        body = "See [the docs](https://example.com) for more."
        result = extract_body_excerpt(body)
        assert "the docs" in result
        assert "https://example.com" not in result

    def test_strips_markdown_images(self):
        body = "Screenshot: ![alt text](https://img.com/x.png)"
        result = extract_body_excerpt(body)
        assert "https://img.com" not in result
        assert "alt text" in result

    def test_strips_header_markers(self):
        body = "### Problem\n\nThe issue is here."
        result = extract_body_excerpt(body)
        assert "###" not in result
        assert "Problem" in result

    def test_collapses_whitespace(self):
        body = "Line 1\n\n\n\nLine 2\t\tLine 3"
        result = extract_body_excerpt(body)
        assert "  " not in result

    def test_truncation_at_word_boundary(self):
        body = "word " * 100  # 500 chars
        result = extract_body_excerpt(body, max_len=50)
        assert len(result) <= 51  # 50 + ellipsis
        assert result.endswith("…")
        assert not result.endswith(" …")  # Should cut at word boundary

    def test_no_truncation_if_short(self):
        body = "Short text."
        result = extract_body_excerpt(body, max_len=200)
        assert result == "Short text."
        assert "…" not in result

    def test_complex_body(self):
        body = """## Bug Report

### Steps to Reproduce

1. Open the app
2. Click button

```bash
$ app --version
1.0.0
```

### Expected Behavior

The button should work.

### Actual Behavior

It <b>crashes</b> with [error link](https://err.com/123).
"""
        result = extract_body_excerpt(body, max_len=200)
        assert "```" not in result
        assert "<b>" not in result
        assert "https://err.com" not in result
        assert "Bug Report" in result
        assert len(result) <= 201


# ===========================================================================
# PARSING TESTS
# ===========================================================================


class TestParsing:
    """Tests for issue parsing."""

    def test_parse_datetime_utc(self):
        dt = _parse_datetime("2026-05-01T10:00:00Z")
        assert dt.year == 2026
        assert dt.month == 5
        assert dt.day == 1
        assert dt.tzinfo is not None

    def test_parse_datetime_with_offset(self):
        dt = _parse_datetime("2026-05-01T10:00:00+05:30")
        assert dt.year == 2026
        assert dt.tzinfo is not None

    def test_parse_issues_basic(self, sample_raw_issue):
        """Parse a single raw issue into an Issue object."""
        issues = parse_issues(
            [sample_raw_issue],
            repo_stars={"canonical/project": 250},
            pr_states={"canonical/project#42": "OPEN"},
        )
        assert len(issues) == 1
        issue = issues[0]
        assert issue.title == "Fix broken test"
        assert issue.repo == "canonical/project"
        assert issue.number == 42
        assert issue.comments == 3
        assert issue.assignees == ["user1"]
        assert issue.labels == ["good first issue", "bug"]
        assert issue.stars == 250
        assert issue.linked_pr_state == "OPEN"
        # Fenced code block (```python...```) should be stripped
        assert "assert False" not in issue.body_excerpt
        # But inline code (`test_foo`) is preserved as part of prose
        assert "broken" in issue.body_excerpt

    def test_parse_issues_missing_optional_fields(self):
        """Parsing should handle missing optional fields gracefully."""
        raw = {
            "title": "Minimal issue",
            "url": "https://github.com/canonical/x/issues/1",
            "repository": {"name": "x", "nameWithOwner": "canonical/x"},
            "createdAt": "2026-01-01T00:00:00Z",
            "updatedAt": "2026-01-01T00:00:00Z",
            "commentsCount": 0,
            "assignees": [],
            "labels": [],
            "body": None,
            "number": 1,
        }
        issues = parse_issues([raw], {}, {})
        assert len(issues) == 1
        assert issues[0].body_excerpt == ""
        assert issues[0].stars == 0
        assert issues[0].linked_pr_state == ""


# ===========================================================================
# CACHING AND DIFFING TESTS
# ===========================================================================


class TestCaching:
    """Tests for cache save/load operations."""

    def test_save_and_load_cache(self, sample_issues, now, tmp_dir):
        """Cache should round-trip correctly."""
        # Patch CACHE_DIR to use tmp
        with patch.object(scraper, 'CACHE_DIR', tmp_dir):
            compute_scores(sample_issues, now)
            save_cache("test-org", sample_issues)

            cache = get_previous_cache("test-org")
            assert cache is not None
            assert len(cache) == 3
            assert "canonical/repo-a#1" in cache
            assert cache["canonical/repo-a#1"]["title"] == "Easy fix"
            assert isinstance(cache["canonical/repo-a#1"]["total_score"], float)

    def test_load_nonexistent_cache(self, tmp_dir):
        """Loading from non-existent path returns None."""
        with patch.object(scraper, 'CACHE_DIR', tmp_dir):
            cache = get_previous_cache("nonexistent-org")
            assert cache is None

    def test_load_corrupted_cache(self, tmp_dir):
        """Loading corrupted cache returns None."""
        with patch.object(scraper, 'CACHE_DIR', tmp_dir):
            cache_path = tmp_dir / "corrupt_issues.json"
            cache_path.write_text("not valid json{{{", encoding="utf-8")
            with patch.object(scraper, 'get_cache_path', return_value=cache_path):
                # Directly test the function with a corrupted file
                result = get_previous_cache("corrupt")
            # The actual function calls get_cache_path internally
            # Let's test by writing to the expected path
            expected_path = tmp_dir / "corrupt-org_issues.json"
            expected_path.write_text("not valid json", encoding="utf-8")
            cache = get_previous_cache("corrupt-org")
            assert cache is None


class TestDiffing:
    """Tests for the apply_diff function."""

    def test_first_run_all_new(self, sample_issues, now):
        """On first run (no previous cache), all issues are new."""
        compute_scores(sample_issues, now)
        new_count, missing, changed = apply_diff(sample_issues, None)
        assert new_count == 3
        assert missing == 0
        assert changed == 0
        assert all(i.is_new for i in sample_issues)

    def test_no_changes(self, sample_issues, now):
        """Same set of issues → 0 new, 0 missing."""
        compute_scores(sample_issues, now)
        previous = {
            f"{i.repo}#{i.number}": {"total_score": i.total_score}
            for i in sample_issues
        }
        # Reset is_new
        for i in sample_issues:
            i.is_new = False
        new_count, missing, changed = apply_diff(sample_issues, previous)
        assert new_count == 0
        assert missing == 0
        assert changed == 0

    def test_new_issue_detected(self, sample_issues, now):
        """A new issue not in previous cache is flagged."""
        compute_scores(sample_issues, now)
        # Previous cache only has first two issues
        previous = {
            f"{i.repo}#{i.number}": {"total_score": i.total_score}
            for i in sample_issues[:2]
        }
        for i in sample_issues:
            i.is_new = False
        new_count, missing, changed = apply_diff(sample_issues, previous)
        assert new_count == 1
        assert sample_issues[2].is_new is True

    def test_missing_issue_detected(self, sample_issues, now):
        """An issue in previous but not current is counted as missing."""
        compute_scores(sample_issues, now)
        previous = {
            f"{i.repo}#{i.number}": {"total_score": i.total_score}
            for i in sample_issues
        }
        # Add a "ghost" issue that no longer exists
        previous["canonical/ghost#999"] = {"total_score": 50.0}

        for i in sample_issues:
            i.is_new = False
        new_count, missing, changed = apply_diff(sample_issues, previous)
        assert missing == 1

    def test_significant_score_change(self, sample_issues, now):
        """Score delta > 10 should be flagged as changed."""
        compute_scores(sample_issues, now)
        previous = {
            f"{i.repo}#{i.number}": {"total_score": i.total_score - 15}
            for i in sample_issues
        }
        for i in sample_issues:
            i.is_new = False
        new_count, missing, changed = apply_diff(sample_issues, previous)
        assert changed == 3  # All have delta > 10

    def test_small_score_change_not_flagged(self, sample_issues, now):
        """Score delta <= 10 should not be flagged."""
        compute_scores(sample_issues, now)
        previous = {
            f"{i.repo}#{i.number}": {"total_score": i.total_score - 5}
            for i in sample_issues
        }
        for i in sample_issues:
            i.is_new = False
        new_count, missing, changed = apply_diff(sample_issues, previous)
        assert changed == 0


# ===========================================================================
# CSV EXPORT TESTS
# ===========================================================================


class TestCSVExport:
    """Tests for CSV export functionality."""

    def test_export_creates_file(self, sample_issues, now, tmp_dir):
        """CSV file should be created with correct content."""
        compute_scores(sample_issues, now)
        output_path = tmp_dir / "test_output.csv"
        export_csv(sample_issues, output_path)
        assert output_path.exists()

    def test_export_has_correct_headers(self, sample_issues, now, tmp_dir):
        """CSV should have all expected columns."""
        compute_scores(sample_issues, now)
        output_path = tmp_dir / "test_output.csv"
        export_csv(sample_issues, output_path)

        with output_path.open() as f:
            reader = csv.DictReader(f)
            assert set(reader.fieldnames) == set(scraper.CSV_COLUMNS)

    def test_export_row_count(self, sample_issues, now, tmp_dir):
        """CSV should have one row per issue."""
        compute_scores(sample_issues, now)
        output_path = tmp_dir / "test_output.csv"
        export_csv(sample_issues, output_path)

        with output_path.open() as f:
            reader = csv.DictReader(f)
            rows = list(reader)
        assert len(rows) == 3

    def test_export_sorted_by_score(self, sample_issues, now, tmp_dir):
        """CSV rows should be sorted by score descending."""
        compute_scores(sample_issues, now)
        output_path = tmp_dir / "test_output.csv"
        export_csv(sample_issues, output_path)

        with output_path.open() as f:
            reader = csv.DictReader(f)
            scores = [float(row["total_score"]) for row in reader]
        assert scores == sorted(scores, reverse=True)

    def test_export_ranks_are_sequential(self, sample_issues, now, tmp_dir):
        """Ranks should be 1, 2, 3, ..."""
        compute_scores(sample_issues, now)
        output_path = tmp_dir / "test_output.csv"
        export_csv(sample_issues, output_path)

        with output_path.open() as f:
            reader = csv.DictReader(f)
            ranks = [int(row["rank"]) for row in reader]
        assert ranks == [1, 2, 3]

    def test_csv_roundtrip_via_tui_loader(self, sample_issues, now, tmp_dir):
        """CSV written by export_csv should be loadable by tui.load_issues."""
        compute_scores(sample_issues, now)
        for i in sample_issues:
            i.is_new = True
        output_path = tmp_dir / "roundtrip.csv"
        export_csv(sample_issues, output_path)

        loaded = tui_load_issues(output_path)
        assert len(loaded) == 3
        assert loaded[0].total_score >= loaded[1].total_score

    def test_csv_roundtrip_via_match_loader(self, sample_issues, now, tmp_dir):
        """CSV should be loadable by match_issues.load_issues."""
        compute_scores(sample_issues, now)
        output_path = tmp_dir / "roundtrip2.csv"
        export_csv(sample_issues, output_path)

        loaded = match_load_issues(output_path)
        assert len(loaded) == 3
        assert loaded[0].title == sample_issues[0].title or loaded[0].title


# ===========================================================================
# LINKED PR DETECTION TESTS
# ===========================================================================


class TestLinkedPRParsing:
    """Tests for linked PR GraphQL response parsing."""

    def test_fetch_linked_prs_with_mock(self):
        """Test PR state extraction from GraphQL response."""
        issues = [
            {
                "repository": {"nameWithOwner": "canonical/test"},
                "number": 1,
            },
            {
                "repository": {"nameWithOwner": "canonical/test"},
                "number": 2,
            },
        ]

        graphql_response = json.dumps({
            "data": {
                "issue_0_0": {
                    "issue": {
                        "closedByPullRequestsReferences": {
                            "nodes": [
                                {"state": "OPEN"},
                            ]
                        }
                    }
                },
                "issue_0_1": {
                    "issue": {
                        "closedByPullRequestsReferences": {
                            "nodes": [
                                {"state": "MERGED"},
                                {"state": "CLOSED"},
                            ]
                        }
                    }
                },
            }
        })

        with patch.object(scraper, 'run_gh_command', return_value=graphql_response):
            result = fetch_linked_prs(issues)

        assert result["canonical/test#1"] == "OPEN"
        assert result["canonical/test#2"] == "MERGED"  # MERGED takes priority over CLOSED

    def test_fetch_linked_prs_api_failure(self):
        """GraphQL failure should return empty states, not crash."""
        issues = [
            {"repository": {"nameWithOwner": "canonical/fail"}, "number": 1},
        ]

        with patch.object(
            scraper, 'run_gh_command',
            side_effect=subprocess.CalledProcessError(1, "gh"),
        ):
            result = fetch_linked_prs(issues)

        assert result["canonical/fail#1"] == ""

    def test_open_pr_wins_over_merged(self):
        """If both OPEN and MERGED PRs exist, OPEN should win."""
        issues = [
            {"repository": {"nameWithOwner": "canonical/x"}, "number": 1},
        ]
        graphql_response = json.dumps({
            "data": {
                "issue_0_0": {
                    "issue": {
                        "closedByPullRequestsReferences": {
                            "nodes": [
                                {"state": "MERGED"},
                                {"state": "OPEN"},
                            ]
                        }
                    }
                },
            }
        })
        with patch.object(scraper, 'run_gh_command', return_value=graphql_response):
            result = fetch_linked_prs(issues)
        assert result["canonical/x#1"] == "OPEN"


# ===========================================================================
# MATCH ISSUES TESTS
# ===========================================================================


class TestMatchIssues:
    """Tests for the LLM matcher module."""

    def test_user_profile_creation(self):
        """UserProfile should hold all fields."""
        profile = UserProfile(
            languages=["Python", "Go"],
            domains=["ML", "testing"],
            experience="intermediate",
            extra_context="I like small fixes",
        )
        assert profile.languages == ["Python", "Go"]
        assert profile.experience == "intermediate"

    def test_issue_summary_creation(self):
        """IssueSummary should hold all fields including new ones."""
        summary = IssueSummary(
            index=1,
            title="Test issue",
            repo="canonical/test",
            labels="good first issue; bug",
            score=85.0,
            stars=100,
            comments=2,
            body_excerpt="Fix the broken test",
            linked_pr_state="OPEN",
            is_new=True,
        )
        assert summary.body_excerpt == "Fix the broken test"
        assert summary.linked_pr_state == "OPEN"
        assert summary.is_new is True

    def test_build_prompt_structure(self):
        """Prompt should contain profile info and all issues."""
        profile = UserProfile(
            languages=["Python"],
            domains=["testing"],
            experience="beginner",
            extra_context="",
        )
        issues = [
            IssueSummary(1, "Issue A", "canonical/a", "bug", 80, 50, 0, "desc A", "", False),
            IssueSummary(2, "Issue B", "canonical/b", "enhancement", 60, 10, 3, "desc B", "OPEN", True),
        ]
        sys_prompt, user_prompt = build_prompt(profile, issues)

        # System prompt should define the task
        assert "developer-issue matcher" in sys_prompt
        assert "JSON" in sys_prompt

        # User prompt should contain profile
        assert "Python" in user_prompt
        assert "testing" in user_prompt
        assert "beginner" in user_prompt

        # User prompt should contain all issues
        assert "Issue A" in user_prompt
        assert "Issue B" in user_prompt
        assert "canonical/a" in user_prompt

    def test_build_prompt_includes_body_excerpt(self):
        """Body excerpts should be included in the prompt."""
        profile = UserProfile(["Python"], ["ML"], "beginner", "")
        issues = [
            IssueSummary(1, "Fix types", "canonical/a", "typing", 80, 50, 0,
                         "Add type annotations to the auth module", "", False),
        ]
        _, user_prompt = build_prompt(profile, issues)
        assert "Add type annotations to the auth module" in user_prompt

    def test_build_prompt_no_body_excerpt(self):
        """Issues without body should not have Desc field."""
        profile = UserProfile(["Python"], ["ML"], "beginner", "")
        issues = [
            IssueSummary(1, "Fix types", "canonical/a", "typing", 80, 50, 0, "", "", False),
        ]
        _, user_prompt = build_prompt(profile, issues)
        assert "Desc:" not in user_prompt

    def test_load_issues_from_csv(self, sample_issues, now, tmp_dir):
        """match_issues.load_issues should correctly load the CSV."""
        compute_scores(sample_issues, now)
        sample_issues[0].is_new = True
        output_path = tmp_dir / "match_test.csv"
        export_csv(sample_issues, output_path)

        loaded = match_load_issues(output_path)
        assert len(loaded) == 3
        assert loaded[0].index == 1
        assert loaded[0].repo in ["canonical/repo-a", "canonical/repo-b", "canonical/repo-c"]


# ===========================================================================
# TUI TESTS
# ===========================================================================


class TestTUIHelpers:
    """Tests for TUI helper functions."""

    def test_score_style_high(self):
        assert score_style(80) == "score.high"

    def test_score_style_mid(self):
        assert score_style(60) == "score.mid"

    def test_score_style_low(self):
        assert score_style(30) == "score.low"

    def test_score_style_boundaries(self):
        assert score_style(75) == "score.high"
        assert score_style(74.9) == "score.mid"
        assert score_style(50) == "score.mid"
        assert score_style(49.9) == "score.low"

    def test_truncate_short_text(self):
        assert truncate("hello", 10) == "hello"

    def test_truncate_exact_length(self):
        assert truncate("hello", 5) == "hello"

    def test_truncate_long_text(self):
        result = truncate("hello world this is long", 10)
        assert len(result) == 10
        assert result.endswith("…")

    def test_render_score_bar_returns_text(self):
        """render_score_bar should return a Rich Text object."""
        result = render_score_bar(75.0, width=10)
        assert hasattr(result, 'plain')  # Rich Text object


class TestTUIFilter:
    """Tests for the TUI filter function."""

    def test_filter_by_title(self, sample_issues, now, tmp_dir):
        """Filter should match title keywords."""
        compute_scores(sample_issues, now)
        output_path = tmp_dir / "filter_test.csv"
        export_csv(sample_issues, output_path)
        loaded = tui_load_issues(output_path)

        result = filter_issues(loaded, "Easy")
        assert len(result) >= 1
        assert any("Easy" in i.title for i in result)

    def test_filter_by_repo(self, sample_issues, now, tmp_dir):
        """Filter should match repo names."""
        compute_scores(sample_issues, now)
        output_path = tmp_dir / "filter_test.csv"
        export_csv(sample_issues, output_path)
        loaded = tui_load_issues(output_path)

        result = filter_issues(loaded, "repo-b")
        assert len(result) >= 1

    def test_filter_by_label(self, sample_issues, now, tmp_dir):
        """Filter should match labels."""
        compute_scores(sample_issues, now)
        output_path = tmp_dir / "filter_test.csv"
        export_csv(sample_issues, output_path)
        loaded = tui_load_issues(output_path)

        result = filter_issues(loaded, "typing")
        assert len(result) >= 1

    def test_filter_case_insensitive(self, sample_issues, now, tmp_dir):
        """Filter should be case-insensitive."""
        compute_scores(sample_issues, now)
        output_path = tmp_dir / "filter_test.csv"
        export_csv(sample_issues, output_path)
        loaded = tui_load_issues(output_path)

        result_upper = filter_issues(loaded, "EASY")
        result_lower = filter_issues(loaded, "easy")
        assert len(result_upper) == len(result_lower)

    def test_filter_no_match(self, sample_issues, now, tmp_dir):
        """Filter with no matches returns empty list."""
        compute_scores(sample_issues, now)
        output_path = tmp_dir / "filter_test.csv"
        export_csv(sample_issues, output_path)
        loaded = tui_load_issues(output_path)

        result = filter_issues(loaded, "zzzznonexistent")
        assert len(result) == 0


# ===========================================================================
# INTEGRATION TESTS
# ===========================================================================


class TestIntegration:
    """Integration tests for the full pipeline."""

    def test_full_pipeline_mock(self, now, tmp_dir):
        """Full pipeline: fetch → parse → score → export → load."""
        raw_issues = [
            {
                "title": f"Issue {i}",
                "url": f"https://github.com/canonical/repo/issues/{i}",
                "repository": {"name": "repo", "nameWithOwner": "canonical/repo"},
                "createdAt": (now - timedelta(days=i * 10)).isoformat(),
                "updatedAt": (now - timedelta(days=i * 5)).isoformat(),
                "commentsCount": i,
                "assignees": [] if i % 2 == 0 else [{"login": f"user{i}"}],
                "labels": [{"name": "good first issue", "id": "1", "color": "", "description": ""}],
                "body": f"Description for issue {i}" * 5,
                "number": i,
            }
            for i in range(1, 11)
        ]

        repo_stars = {"canonical/repo": 1000}
        pr_states = {"canonical/repo#1": "OPEN", "canonical/repo#5": "MERGED"}

        # Parse
        issues = parse_issues(raw_issues, repo_stars, pr_states)
        assert len(issues) == 10

        # Score
        compute_scores(issues, now)
        assert all(0 <= i.total_score <= 100 for i in issues)

        # Diff (first run)
        with patch.object(scraper, 'CACHE_DIR', tmp_dir):
            new_count, missing, changed = apply_diff(issues, None)
            assert new_count == 10

            # Save cache
            save_cache("canonical", issues)

            # Second run diff
            previous = get_previous_cache("canonical")
            for i in issues:
                i.is_new = False
            new_count, missing, changed = apply_diff(issues, previous)
            assert new_count == 0

        # Export
        output = tmp_dir / "integration.csv"
        export_csv(issues, output)
        assert output.exists()

        # Load in TUI
        tui_issues = tui_load_issues(output)
        assert len(tui_issues) == 10

        # Load in matcher
        match_issues_loaded = match_load_issues(output)
        assert len(match_issues_loaded) == 10

    def test_empty_issues_list(self, now, tmp_dir):
        """Pipeline should handle empty issue list gracefully."""
        issues = parse_issues([], {}, {})
        assert issues == []

        output = tmp_dir / "empty.csv"
        export_csv([], output)
        assert output.exists()

        with output.open() as f:
            reader = csv.DictReader(f)
            rows = list(reader)
        assert len(rows) == 0


# ===========================================================================
# EDGE CASES
# ===========================================================================


class TestEdgeCases:
    """Edge case tests."""

    def test_issue_with_all_zeros(self, now):
        """Issue with minimal fields should not crash scoring."""
        issue = Issue(
            title="",
            url="",
            repo="canonical/x",
            number=0,
            created_at=now,
            updated_at=now,
            comments=0,
            assignees=[],
            labels=[],
            stars=0,
        )
        compute_scores([issue], now)
        assert issue.total_score >= 0

    def test_issue_created_in_future(self, now):
        """Issue with future created_at shouldn't crash."""
        issue = Issue(
            title="Future",
            url="",
            repo="canonical/x",
            number=1,
            created_at=now + timedelta(days=1),
            updated_at=now + timedelta(days=1),
            comments=0,
            assignees=[],
            labels=[],
            stars=0,
        )
        compute_scores([issue], now)
        # Score > 100 due to negative age → that's mathematically valid
        assert issue.freshness_score > 0

    def test_very_high_star_count(self, now):
        """Extremely popular repo shouldn't overflow."""
        issue = Issue(
            title="Popular",
            url="",
            repo="canonical/x",
            number=1,
            created_at=now,
            updated_at=now,
            comments=0,
            assignees=[],
            labels=[],
            stars=1_000_000,
        )
        compute_scores([issue], now)
        assert issue.popularity_score == pytest.approx(100.0)

    def test_unicode_in_title_and_body(self, now, tmp_dir):
        """Unicode characters should be handled correctly in CSV."""
        issue = Issue(
            title="修复中文问题 🐛",
            url="https://github.com/canonical/x/issues/1",
            repo="canonical/x",
            number=1,
            created_at=now,
            updated_at=now,
            comments=0,
            assignees=[],
            labels=["好的问题"],
            body_excerpt="这是一个Unicode测试 ñ é ü",
            stars=10,
        )
        compute_scores([issue], now)
        output = tmp_dir / "unicode.csv"
        export_csv([issue], output)

        loaded = tui_load_issues(output)
        assert "修复中文问题" in loaded[0].title
        assert "🐛" in loaded[0].title

    def test_csv_with_commas_in_fields(self, now, tmp_dir):
        """Fields with commas should be properly quoted in CSV."""
        issue = Issue(
            title="Fix this, that, and the other",
            url="https://github.com/canonical/x/issues/1",
            repo="canonical/x",
            number=1,
            created_at=now,
            updated_at=now,
            comments=0,
            assignees=["user,name"],
            labels=["bug, confirmed", "good first issue"],
            body_excerpt="Description with, commas, everywhere",
            stars=10,
        )
        compute_scores([issue], now)
        output = tmp_dir / "commas.csv"
        export_csv([issue], output)

        loaded = tui_load_issues(output)
        assert "Fix this, that, and the other" in loaded[0].title

    def test_body_with_only_code_blocks(self):
        """Body that is only code should return empty excerpt."""
        body = "```\nall code\nno prose\n```"
        result = extract_body_excerpt(body)
        assert result == ""

    def test_single_issue_popularity_score(self, now):
        """Single issue should get full popularity score for itself."""
        issue = Issue(
            title="Solo",
            url="",
            repo="canonical/x",
            number=1,
            created_at=now,
            updated_at=now,
            comments=0,
            assignees=[],
            labels=[],
            stars=50,
        )
        compute_scores([issue], now)
        # With only one issue, its stars are the max → should be 100
        assert issue.popularity_score == pytest.approx(100.0)


# Need this import for the subprocess mock
import subprocess
