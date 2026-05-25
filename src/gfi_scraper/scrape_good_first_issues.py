#!/usr/bin/env python3
"""Scrape and rank 'good first issue' issues from Canonical's GitHub repositories.

Uses the `gh` CLI for authenticated GitHub API access. Fetches all open issues
labeled "good first issue" under the canonical org, enriches them with issue
bodies and linked PR status, ranks them by a composite heuristic, caches results,
diffs against previous runs, and exports to CSV.

Features:
    - Fetches issue bodies for richer LLM context
    - Detects linked PRs as a competition signal via GraphQL
    - Caches results and diffs against previous runs (highlights new issues)
    - Auto-refresh mode with configurable interval

Usage:
    python3 scrape_good_first_issues.py [--output FILE] [--org ORG]
    python3 scrape_good_first_issues.py --watch --interval 6  # refresh every 6 hours
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import logging
import math
import re
import signal
import subprocess
import sys
import time
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from pathlib import Path

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

DEFAULT_ORG = "canonical"
DEFAULT_OUTPUT = "good_first_issues.csv"
CACHE_DIR = Path(".cache")
SEARCH_LIMIT = 500
GRAPHQL_BATCH_SIZE = 50  # Issues per GraphQL query for PR detection

# Scoring weights (must sum to 1.0)
WEIGHT_FRESHNESS = 0.25
WEIGHT_COMPETITION = 0.25
WEIGHT_AVAILABILITY = 0.20
WEIGHT_POPULARITY = 0.15
WEIGHT_ACTIVITY = 0.10
WEIGHT_PR_STATUS = 0.05

# Scoring parameters
FRESHNESS_HALF_LIFE_DAYS = 180
COMPETITION_CAP = 10
STALENESS_THRESHOLD_DAYS = 365
BODY_EXCERPT_LENGTH = 200  # Chars to keep for LLM context

# Watch mode defaults
DEFAULT_INTERVAL_HOURS = 6
MAX_WATCH_RUNS = 100

logging.basicConfig(
    level=logging.INFO,
    format="%(levelname)s: %(message)s",
)
logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Repo filtering helpers
# ---------------------------------------------------------------------------


def load_repos_file(path: Path) -> list[str]:
    """Load repo names from a file (one per line, # comments, blank lines ignored)."""
    repos: list[str] = []
    with path.open("r", encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            repos.append(line)
    return repos


def normalize_repos(repos: list[str], org: str) -> list[str]:
    """Normalize repo names to owner/repo format. Bare names get org prefix."""
    normalized: list[str] = []
    seen: set[str] = set()
    for repo in repos:
        if "/" not in repo:
            repo = f"{org}/{repo}"
        repo_lower = repo.lower()
        if repo_lower not in seen:
            seen.add(repo_lower)
            normalized.append(repo)
    return normalized


def repos_cache_key(repos: list[str] | None) -> str:
    """Generate a cache key suffix based on the repo filter."""
    if not repos:
        return ""
    joined = ",".join(sorted(r.lower() for r in repos))
    return "_" + hashlib.sha256(joined.encode()).hexdigest()[:12]


# ---------------------------------------------------------------------------
# Data model
# ---------------------------------------------------------------------------


@dataclass
class Issue:
    """Represents a GitHub issue with scoring metadata."""

    title: str
    url: str
    repo: str
    number: int
    created_at: datetime
    updated_at: datetime
    comments: int
    assignees: list[str]
    labels: list[str]
    body_excerpt: str = ""
    body_full: str = ""  # Full body for quality assessment (not exported)
    stars: int = 0
    linked_pr_state: str = ""  # "", "OPEN", "MERGED", "CLOSED"
    is_new: bool = False
    score_delta: float = 0.0

    # Computed scores (0-100 each)
    freshness_score: float = 0.0
    competition_score: float = 0.0
    availability_score: float = 0.0
    popularity_score: float = 0.0
    activity_score: float = 0.0
    pr_score: float = 0.0
    total_score: float = 0.0

    # Quality scores (0-100 each)
    quality_score: float = 0.0
    quality_description: float = 0.0
    quality_scope: float = 0.0
    quality_mentoring: float = 0.0
    quality_actionability: float = 0.0


# ---------------------------------------------------------------------------
# GitHub data fetching
# ---------------------------------------------------------------------------


def run_gh_command(args: list[str], timeout: int = 120) -> str:
    """Execute a `gh` CLI command and return stdout."""
    cmd = ["gh"] + args
    logger.debug("Running: %s", " ".join(cmd))
    result = subprocess.run(
        cmd,
        capture_output=True,
        text=True,
        check=True,
        timeout=timeout,
    )
    return result.stdout


def fetch_issues(org: str, repos: list[str] | None = None) -> list[dict]:
    """Fetch all open 'good first issue' issues, optionally scoped to specific repos."""
    fields = (
        "title,url,repository,createdAt,updatedAt,"
        "commentsCount,assignees,labels,state,body,number"
    )

    if repos:
        logger.info("Fetching good first issues for %d specific repos...", len(repos))
        all_issues: list[dict] = []
        for repo in repos:
            try:
                output = run_gh_command([
                    "search", "issues",
                    "--label", "good first issue",
                    "--repo", repo,
                    "--state", "open",
                    "--limit", str(SEARCH_LIMIT),
                    "--json", fields,
                ])
                issues = json.loads(output)
                all_issues.extend(issues)
                if issues:
                    logger.debug("  %s: %d issues", repo, len(issues))
            except subprocess.CalledProcessError as exc:
                logger.warning("  Failed to fetch from %s: %s", repo, exc)
        logger.info("Fetched %d issues from %d repos.", len(all_issues), len(repos))
        return all_issues
    else:
        logger.info("Fetching good first issues for org '%s'...", org)
        output = run_gh_command([
            "search", "issues",
            "--label", "good first issue",
            "--owner", org,
            "--state", "open",
            "--limit", str(SEARCH_LIMIT),
            "--json", fields,
        ])
        issues = json.loads(output)
        logger.info("Fetched %d issues.", len(issues))
        return issues


def fetch_repo_stars(repos: set[str]) -> dict[str, int]:
    """Fetch stargazer counts for a set of repositories."""
    logger.info("Fetching star counts for %d unique repositories...", len(repos))
    stars: dict[str, int] = {}
    for repo in sorted(repos):
        try:
            output = run_gh_command([
                "api", f"repos/{repo}",
                "--jq", ".stargazers_count",
            ])
            stars[repo] = int(output.strip())
        except (subprocess.CalledProcessError, ValueError) as exc:
            logger.warning("Could not fetch stars for %s: %s", repo, exc)
            stars[repo] = 0
    return stars


def fetch_linked_prs(issues: list[dict]) -> dict[str, str]:
    """Fetch linked PR state for issues using GraphQL (batched).

    Returns:
        Dict mapping "owner/repo#number" to PR state ("OPEN", "MERGED", "CLOSED", or "").
    """
    logger.info("Checking linked PRs for %d issues via GraphQL...", len(issues))
    pr_states: dict[str, str] = {}

    # Group issues by repo for efficient querying
    batches: list[list[dict]] = []
    for i in range(0, len(issues), GRAPHQL_BATCH_SIZE):
        batches.append(issues[i:i + GRAPHQL_BATCH_SIZE])

    for batch_idx, batch in enumerate(batches):
        query_parts: list[str] = []
        aliases: list[tuple[str, str, int]] = []  # (alias, repo_full, number)

        for idx, issue in enumerate(batch):
            repo_full = issue["repository"]["nameWithOwner"]
            owner, name = repo_full.split("/")
            number = issue["number"]
            alias = f"issue_{batch_idx}_{idx}"
            aliases.append((alias, repo_full, number))

            query_parts.append(f"""
                {alias}: repository(owner: "{owner}", name: "{name}") {{
                    issue(number: {number}) {{
                        closedByPullRequestsReferences(first: 5, includeClosedPrs: true, orderByState: true) {{
                            nodes {{
                                state
                            }}
                        }}
                    }}
                }}
            """)

        query = "query {\n" + "\n".join(query_parts) + "\n}"

        try:
            output = run_gh_command(["api", "graphql", "-f", f"query={query}"], timeout=60)
            data = json.loads(output).get("data", {})

            for alias, repo_full, number in aliases:
                key = f"{repo_full}#{number}"
                repo_data = data.get(alias, {})
                issue_data = repo_data.get("issue", {}) if repo_data else {}
                closing_refs = issue_data.get(
                    "closedByPullRequestsReferences", {}
                ).get("nodes", [])

                # Find the most relevant PR state (prefer OPEN as competition signal)
                best_state = ""
                for node in closing_refs:
                    state = node.get("state", "")
                    if state == "OPEN":
                        best_state = "OPEN"
                        break  # Open PR = definitive competition
                    elif state == "MERGED" and best_state != "OPEN":
                        best_state = "MERGED"
                    elif state == "CLOSED" and not best_state:
                        best_state = "CLOSED"

                pr_states[key] = best_state

        except (subprocess.CalledProcessError, json.JSONDecodeError) as exc:
            logger.warning("GraphQL batch %d failed: %s", batch_idx, exc)
            for _, repo_full, number in aliases:
                pr_states[f"{repo_full}#{number}"] = ""

    linked_count = sum(1 for v in pr_states.values() if v)
    logger.info("Found %d issues with linked PRs.", linked_count)
    return pr_states


# ---------------------------------------------------------------------------
# Body excerpt extraction
# ---------------------------------------------------------------------------


def extract_body_excerpt(body: str | None, max_len: int = BODY_EXCERPT_LENGTH) -> str:
    """Extract a meaningful excerpt from issue body, stripping noise."""
    if not body:
        return ""

    # Remove code blocks
    text = re.sub(r"```[\s\S]*?```", "", body)
    # Remove HTML tags
    text = re.sub(r"<[^>]+>", "", text)
    # Remove markdown image/links syntax but keep text
    text = re.sub(r"!\[([^\]]*)\]\([^)]*\)", r"\1", text)
    text = re.sub(r"\[([^\]]*)\]\([^)]*\)", r"\1", text)
    # Remove markdown headers markers
    text = re.sub(r"^#{1,6}\s*", "", text, flags=re.MULTILINE)
    # Collapse whitespace
    text = re.sub(r"\s+", " ", text).strip()

    if len(text) > max_len:
        # Cut at word boundary
        text = text[:max_len].rsplit(" ", 1)[0] + "…"

    return text


# ---------------------------------------------------------------------------
# Parsing
# ---------------------------------------------------------------------------


def parse_issues(
    raw_issues: list[dict],
    repo_stars: dict[str, int],
    pr_states: dict[str, str],
) -> list[Issue]:
    """Parse raw JSON issues into Issue dataclass instances."""
    issues: list[Issue] = []
    for raw in raw_issues:
        repo_full = raw["repository"]["nameWithOwner"]
        number = raw["number"]
        assignee_names = [a.get("login", "") for a in raw.get("assignees", [])]
        label_names = [lb.get("name", "") for lb in raw.get("labels", [])]
        pr_key = f"{repo_full}#{number}"

        issue = Issue(
            title=raw["title"],
            url=raw["url"],
            repo=repo_full,
            number=number,
            created_at=_parse_datetime(raw["createdAt"]),
            updated_at=_parse_datetime(raw["updatedAt"]),
            comments=raw.get("commentsCount", 0),
            assignees=assignee_names,
            labels=label_names,
            body_excerpt=extract_body_excerpt(raw.get("body")),
            body_full=raw.get("body", "") or "",
            stars=repo_stars.get(repo_full, 0),
            linked_pr_state=pr_states.get(pr_key, ""),
        )
        issues.append(issue)
    return issues


def _parse_datetime(dt_str: str) -> datetime:
    """Parse an ISO 8601 datetime string to a timezone-aware datetime."""
    return datetime.fromisoformat(dt_str.replace("Z", "+00:00"))


# ---------------------------------------------------------------------------
# Scoring
# ---------------------------------------------------------------------------


def compute_scores(issues: list[Issue], now: datetime) -> None:
    """Compute ranking scores for each issue in place."""
    max_log_stars = math.log1p(max((i.stars for i in issues), default=1))

    for issue in issues:
        issue.freshness_score = _score_freshness(issue.created_at, now)
        issue.competition_score = _score_competition(issue.comments)
        issue.availability_score = _score_availability(issue.assignees)
        issue.popularity_score = _score_popularity(issue.stars, max_log_stars)
        issue.activity_score = _score_activity(issue.updated_at, now)
        issue.pr_score = _score_pr_status(issue.linked_pr_state)

        issue.total_score = (
            WEIGHT_FRESHNESS * issue.freshness_score
            + WEIGHT_COMPETITION * issue.competition_score
            + WEIGHT_AVAILABILITY * issue.availability_score
            + WEIGHT_POPULARITY * issue.popularity_score
            + WEIGHT_ACTIVITY * issue.activity_score
            + WEIGHT_PR_STATUS * issue.pr_score
        )


def _score_freshness(created_at: datetime, now: datetime) -> float:
    age_days = (now - created_at).total_seconds() / 86400
    return 100.0 * (0.5 ** (age_days / FRESHNESS_HALF_LIFE_DAYS))


def _score_competition(comments: int) -> float:
    if comments >= COMPETITION_CAP:
        return 0.0
    return 100.0 * (1 - comments / COMPETITION_CAP)


def _score_availability(assignees: list[str]) -> float:
    if not assignees:
        return 100.0
    return max(0.0, 100.0 * (0.3 ** len(assignees)))


def _score_popularity(stars: int, max_log_stars: float) -> float:
    if max_log_stars == 0:
        return 50.0
    return 100.0 * (math.log1p(stars) / max_log_stars)


def _score_activity(updated_at: datetime, now: datetime) -> float:
    days_since_update = (now - updated_at).total_seconds() / 86400
    if days_since_update <= 30:
        return 100.0
    if days_since_update >= STALENESS_THRESHOLD_DAYS:
        return 0.0
    return 100.0 * (1 - (days_since_update - 30) / (STALENESS_THRESHOLD_DAYS - 30))


def _score_pr_status(pr_state: str) -> float:
    """Score based on linked PR state. No PR = best, open PR = worst."""
    if not pr_state:
        return 100.0  # No linked PR — fully available
    if pr_state == "CLOSED":
        return 80.0   # PR was closed (abandoned attempt) — still available
    if pr_state == "MERGED":
        return 30.0   # PR merged — issue may already be resolved
    if pr_state == "OPEN":
        return 10.0   # Open PR — active competition
    return 50.0


def compute_quality(issues: list[Issue]) -> None:
    """Compute quality heuristic scores for each issue in place."""
    from gfi_scraper.assess_quality import compute_quality_scores

    for issue in issues:
        scores = compute_quality_scores(issue.title, issue.body_full, issue.labels)
        issue.quality_score = scores.overall_score
        issue.quality_description = scores.description_score
        issue.quality_scope = scores.scope_score
        issue.quality_mentoring = scores.mentoring_score
        issue.quality_actionability = scores.actionability_score


# ---------------------------------------------------------------------------
# Caching and diffing
# ---------------------------------------------------------------------------


def get_cache_path(org: str, repos: list[str] | None = None) -> Path:
    """Get the cache file path for the given org/repo scope."""
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    suffix = repos_cache_key(repos)
    return CACHE_DIR / f"{org}{suffix}_issues.json"


def get_previous_cache(org: str, repos: list[str] | None = None) -> dict[str, dict] | None:
    """Load the previous cache, keyed by 'repo#number'."""
    cache_path = get_cache_path(org, repos)
    if not cache_path.exists():
        return None
    try:
        data = json.loads(cache_path.read_text(encoding="utf-8"))
        return data.get("issues", {})
    except (json.JSONDecodeError, KeyError):
        return None


def save_cache(org: str, issues: list[Issue], repos: list[str] | None = None) -> None:
    """Save current results to cache."""
    cache_path = get_cache_path(org, repos)
    cache_data = {
        "org": org,
        "fetched_at": datetime.now(timezone.utc).isoformat(),
        "count": len(issues),
        "issues": {},
    }
    for issue in issues:
        key = f"{issue.repo}#{issue.number}"
        cache_data["issues"][key] = {
            "title": issue.title,
            "url": issue.url,
            "repo": issue.repo,
            "number": issue.number,
            "total_score": round(issue.total_score, 2),
            "created_at": issue.created_at.isoformat(),
            "linked_pr_state": issue.linked_pr_state,
        }
    cache_path.write_text(json.dumps(cache_data, indent=2), encoding="utf-8")
    logger.info("Cache saved to %s", cache_path)


def apply_diff(issues: list[Issue], previous: dict[str, dict] | None) -> tuple[int, int, int]:
    """Mark issues as new and compute score deltas.

    Returns:
        Tuple of (new_count, missing_count, changed_count).
    """
    if previous is None:
        # First run — all issues are "new"
        for issue in issues:
            issue.is_new = True
        return len(issues), 0, 0

    current_keys = set()
    new_count = 0
    changed_count = 0

    for issue in issues:
        key = f"{issue.repo}#{issue.number}"
        current_keys.add(key)

        if key not in previous:
            issue.is_new = True
            new_count += 1
        else:
            prev_score = previous[key].get("total_score", 0)
            issue.score_delta = issue.total_score - prev_score
            if abs(issue.score_delta) > 10:
                changed_count += 1

    missing_count = len(set(previous.keys()) - current_keys)

    return new_count, missing_count, changed_count


# ---------------------------------------------------------------------------
# CSV export
# ---------------------------------------------------------------------------

CSV_COLUMNS = [
    "rank",
    "total_score",
    "quality_score",
    "is_new",
    "score_delta",
    "title",
    "url",
    "repo",
    "stars",
    "created_at",
    "comments",
    "assignees",
    "labels",
    "linked_pr_state",
    "body_excerpt",
    "freshness_score",
    "competition_score",
    "availability_score",
    "popularity_score",
    "activity_score",
    "pr_score",
    "quality_description",
    "quality_scope",
    "quality_mentoring",
    "quality_actionability",
]


def export_csv(issues: list[Issue], output_path: Path) -> None:
    """Write ranked issues to a CSV file."""
    sorted_issues = sorted(issues, key=lambda i: i.total_score, reverse=True)

    with output_path.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=CSV_COLUMNS)
        writer.writeheader()

        for rank, issue in enumerate(sorted_issues, start=1):
            writer.writerow({
                "rank": rank,
                "total_score": f"{issue.total_score:.2f}",
                "is_new": "🆕" if issue.is_new else "",
                "score_delta": f"{issue.score_delta:+.1f}" if issue.score_delta else "",
                "title": issue.title,
                "url": issue.url,
                "repo": issue.repo,
                "stars": issue.stars,
                "created_at": issue.created_at.strftime("%Y-%m-%d"),
                "comments": issue.comments,
                "assignees": "; ".join(issue.assignees) if issue.assignees else "",
                "labels": "; ".join(issue.labels),
                "linked_pr_state": issue.linked_pr_state,
                "body_excerpt": issue.body_excerpt,
                "freshness_score": f"{issue.freshness_score:.1f}",
                "competition_score": f"{issue.competition_score:.1f}",
                "availability_score": f"{issue.availability_score:.1f}",
                "popularity_score": f"{issue.popularity_score:.1f}",
                "activity_score": f"{issue.activity_score:.1f}",
                "pr_score": f"{issue.pr_score:.1f}",
                "quality_score": f"{issue.quality_score:.1f}",
                "quality_description": f"{issue.quality_description:.1f}",
                "quality_scope": f"{issue.quality_scope:.1f}",
                "quality_mentoring": f"{issue.quality_mentoring:.1f}",
                "quality_actionability": f"{issue.quality_actionability:.1f}",
            })

    logger.info("Exported %d issues to %s", len(sorted_issues), output_path)


# ---------------------------------------------------------------------------
# Main logic
# ---------------------------------------------------------------------------


def run_scrape(org: str, output: str, repos: list[str] | None = None) -> int:
    """Execute a single scrape run. Returns exit code."""
    now = datetime.now(timezone.utc)

    try:
        raw_issues = fetch_issues(org, repos)
    except (subprocess.CalledProcessError, FileNotFoundError) as exc:
        logger.error("Failed to fetch issues: %s", exc)
        return 1

    if not raw_issues:
        logger.warning("No issues found. Nothing to export.")
        return 0

    # Fetch enrichment data
    unique_repos = {issue["repository"]["nameWithOwner"] for issue in raw_issues}
    try:
        repo_stars = fetch_repo_stars(unique_repos)
    except (subprocess.CalledProcessError, FileNotFoundError) as exc:
        logger.warning("Failed to fetch repo stars: %s", exc)
        repo_stars = {}

    try:
        pr_states = fetch_linked_prs(raw_issues)
    except Exception as exc:
        logger.warning("Failed to fetch linked PRs: %s", exc)
        pr_states = {}

    issues = parse_issues(raw_issues, repo_stars, pr_states)

    # Exclude issues that have a merged closing PR (already resolved)
    resolved = [i for i in issues if i.linked_pr_state == "MERGED"]
    if resolved:
        logger.info(
            "Excluding %d issues with merged closing PRs.", len(resolved)
        )
    issues = [i for i in issues if i.linked_pr_state != "MERGED"]

    compute_scores(issues, now)

    # Compute quality scores using full body (available during scrape)
    compute_quality(issues)

    # Diff against previous run
    previous = get_previous_cache(org, repos)
    new_count, missing_count, changed_count = apply_diff(issues, previous)

    # Save cache
    save_cache(org, issues, repos)

    # Export CSV
    output_path = Path(output)
    export_csv(issues, output_path)

    # Print summary
    print(f"\n{'='*70}")
    print(f"  Scrape Complete — {len(issues)} issues from {len(unique_repos)} repos")
    print(f"{'='*70}")

    if previous is not None:
        print(f"\n  📊 Diff vs previous run:")
        print(f"     🆕 {new_count} new issues")
        print(f"     ❌ {missing_count} issues no longer open")
        print(f"     📈 {changed_count} issues with significant score changes")

    # Show new issues if any
    new_issues = sorted(
        [i for i in issues if i.is_new and previous is not None],
        key=lambda i: i.total_score,
        reverse=True,
    )[:5]
    if new_issues:
        print(f"\n  🆕 Newest issues:")
        for issue in new_issues:
            print(f"     [{issue.total_score:.1f}] {issue.title}")
            print(f"     {issue.url}")

    # Show top 5
    top = sorted(issues, key=lambda i: i.total_score, reverse=True)[:5]
    print(f"\n  🏆 Top 5:")
    for i, issue in enumerate(top, 1):
        pr_flag = f" ⚠️PR:{issue.linked_pr_state}" if issue.linked_pr_state else ""
        new_flag = " 🆕" if issue.is_new else ""
        print(f"     {i}. [{issue.total_score:.1f}] {issue.title}{new_flag}{pr_flag}")
        print(f"        {issue.repo} ({issue.stars}⭐) | 💬{issue.comments}")

    return 0


# ---------------------------------------------------------------------------
# Watch mode
# ---------------------------------------------------------------------------


_stop_watch = False


def _handle_sigint(signum, frame):
    global _stop_watch
    _stop_watch = True
    print("\n\n⏹️  Stopping watch mode gracefully...")


def watch_mode(org: str, output: str, interval_hours: float, max_runs: int, repos: list[str] | None = None) -> int:
    """Run the scraper on a schedule."""
    global _stop_watch
    signal.signal(signal.SIGINT, _handle_sigint)

    interval_seconds = interval_hours * 3600
    run_count = 0
    backoff = 1

    scope = f"{len(repos)} repos" if repos else f"org '{org}'"
    print(f"👁️  Watch mode: refreshing {scope} every {interval_hours}h (max {max_runs} runs)")
    print(f"   Press Ctrl+C to stop.\n")

    while not _stop_watch and run_count < max_runs:
        run_count += 1
        print(f"\n{'─'*70}")
        print(f"  Run {run_count}/{max_runs} — {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
        print(f"{'─'*70}")

        exit_code = run_scrape(org, output, repos)

        if exit_code != 0:
            wait = min(backoff * 60, interval_seconds)
            logger.warning("Run failed. Backing off for %d seconds...", wait)
            backoff *= 2
            time.sleep(wait)
            continue

        backoff = 1  # Reset on success

        if run_count < max_runs and not _stop_watch:
            next_run = datetime.now().strftime("%H:%M:%S")
            print(f"\n  ⏰ Next refresh in {interval_hours}h...")
            # Sleep in small increments for responsive Ctrl+C
            for _ in range(int(interval_seconds)):
                if _stop_watch:
                    break
                time.sleep(1)

    print(f"\n✅ Watch mode finished ({run_count} runs completed).")
    return 0


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Scrape and rank 'good first issue' issues from a GitHub org.",
    )
    parser.add_argument(
        "--org",
        default=DEFAULT_ORG,
        help=f"GitHub organization to search (default: {DEFAULT_ORG})",
    )
    parser.add_argument(
        "--repos",
        help="Comma-separated list of repo names to search (e.g. 'observability,cos-lib')",
    )
    parser.add_argument(
        "--repos-file",
        help="Path to a file with repo names (one per line)",
    )
    parser.add_argument(
        "--output", "-o",
        default=DEFAULT_OUTPUT,
        help=f"Output CSV file path (default: {DEFAULT_OUTPUT})",
    )
    parser.add_argument(
        "--watch", "-w",
        action="store_true",
        help="Enable auto-refresh watch mode",
    )
    parser.add_argument(
        "--interval",
        type=float,
        default=DEFAULT_INTERVAL_HOURS,
        help=f"Refresh interval in hours for watch mode (default: {DEFAULT_INTERVAL_HOURS})",
    )
    parser.add_argument(
        "--max-runs",
        type=int,
        default=MAX_WATCH_RUNS,
        help=f"Maximum runs in watch mode (default: {MAX_WATCH_RUNS})",
    )
    parser.add_argument(
        "--cron",
        action="store_true",
        help="Print a crontab entry for scheduled runs and exit",
    )
    return parser.parse_args()


def _resolve_repos(args: argparse.Namespace) -> list[str] | None:
    """Resolve the repos list from CLI args."""
    repos: list[str] = []
    if args.repos:
        repos.extend(r.strip() for r in args.repos.split(",") if r.strip())
    if args.repos_file:
        repos_path = Path(args.repos_file)
        if not repos_path.exists():
            logger.error("Repos file not found: %s", repos_path)
            sys.exit(1)
        repos.extend(load_repos_file(repos_path))
    if not repos:
        return None
    return normalize_repos(repos, args.org)


def main() -> int:
    args = parse_args()
    repos = _resolve_repos(args)

    if args.cron:
        script_path = Path(__file__).resolve()
        repos_flag = ""
        if args.repos:
            repos_flag = f" --repos {args.repos}"
        elif args.repos_file:
            repos_flag = f" --repos-file {args.repos_file}"
        print("# Good First Issue scraper — add to crontab with: crontab -e")
        print(f"0 */6 * * * cd {script_path.parent} && python3 {script_path.name} --org {args.org}{repos_flag} -o {args.output}")
        return 0

    if args.watch:
        return watch_mode(args.org, args.output, args.interval, args.max_runs, repos)

    return run_scrape(args.org, args.output, repos)


if __name__ == "__main__":
    sys.exit(main())
