#!/usr/bin/env python3
"""Discover potential 'good first issues' from all open issues using LLM.

Scrapes ALL open issues from specified repos, uses an LLM to identify
which ones could be good first issues, and generates improved descriptions
to make them beginner-friendly.

Usage:
    gfi-discover --repos-file configs/observability_repos.txt [--top N] [--output FILE]
"""

from __future__ import annotations

import argparse
import csv
import json
import logging
import os
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

DEFAULT_OUTPUT = "discovered_gfis.csv"
DEFAULT_TOP_N = 30
MODEL = "gpt-5.5"
MAX_TOKENS = 4000
ISSUES_PER_REPO = 100  # Max issues to fetch per repo
LLM_BATCH_SIZE = 15  # Issues per LLM call (to stay within token limits)

CACHE_DIR = Path(".cache")
DISCOVER_CACHE_FILE = CACHE_DIR / "discover_gfi_cache.json"


# ---------------------------------------------------------------------------
# Data model
# ---------------------------------------------------------------------------


@dataclass
class RawIssue:
    """An open issue fetched from GitHub (not necessarily a GFI)."""

    title: str
    url: str
    repo: str
    number: int
    body: str
    labels: list[str]
    comments: int
    created_at: str
    updated_at: str
    assignees: list[str]


@dataclass
class DiscoveredGFI:
    """An issue identified by LLM as a potential good first issue."""

    title: str
    url: str
    repo: str
    number: int
    labels: str
    comments: int
    created_at: str
    gfi_score: int  # 1-5 how suitable as GFI
    reason: str  # Why it's a good first issue
    improved_description: str  # LLM-generated improved description
    original_body_excerpt: str


# ---------------------------------------------------------------------------
# GitHub fetching
# ---------------------------------------------------------------------------


def fetch_all_issues(repos: list[str]) -> list[RawIssue]:
    """Fetch all open issues from specified repos (not just GFI-labeled ones)."""
    all_issues: list[RawIssue] = []
    fields = "title,url,repository,createdAt,updatedAt,commentsCount,assignees,labels,body,number"

    for repo in repos:
        logger.info("Fetching issues from %s...", repo)
        try:
            result = subprocess.run(
                [
                    "gh", "search", "issues",
                    "--repo", repo,
                    "--state", "open",
                    "--limit", str(ISSUES_PER_REPO),
                    "--json", fields,
                ],
                capture_output=True, text=True, check=True, timeout=60,
            )
            raw_issues = json.loads(result.stdout)

            for raw in raw_issues:
                # Skip issues already labeled as good first issue
                label_names = [lb.get("name", "").lower() for lb in raw.get("labels", [])]
                if "good first issue" in label_names or "good-first-issue" in label_names:
                    continue

                all_issues.append(RawIssue(
                    title=raw["title"],
                    url=raw["url"],
                    repo=raw["repository"]["nameWithOwner"],
                    number=raw["number"],
                    body=raw.get("body", "") or "",
                    labels=[lb.get("name", "") for lb in raw.get("labels", [])],
                    comments=raw.get("commentsCount", 0),
                    created_at=raw.get("createdAt", ""),
                    updated_at=raw.get("updatedAt", ""),
                    assignees=[a.get("login", "") for a in raw.get("assignees", [])],
                ))

            logger.info("  %s: %d issues (excluding existing GFIs)", repo, len(raw_issues))

        except subprocess.CalledProcessError as exc:
            logger.warning("  Failed to fetch from %s: %s", repo, exc)

    logger.info("Total: %d open issues (non-GFI) across %d repos", len(all_issues), len(repos))
    return all_issues


# ---------------------------------------------------------------------------
# LLM: Identify potential GFIs
# ---------------------------------------------------------------------------


def identify_gfis(issues: list[RawIssue], api_key: str) -> list[dict]:
    """Use LLM to identify which issues could be good first issues.

    Returns list of dicts with: index, gfi_score (1-5), reason
    """
    from openai import OpenAI
    client = OpenAI(api_key=api_key)

    all_results: list[dict] = []

    for batch_start in range(0, len(issues), LLM_BATCH_SIZE):
        batch = issues[batch_start:batch_start + LLM_BATCH_SIZE]
        batch_num = batch_start // LLM_BATCH_SIZE + 1
        total_batches = (len(issues) + LLM_BATCH_SIZE - 1) // LLM_BATCH_SIZE

        print(f"  ⏳ Identifying GFIs batch {batch_num}/{total_batches}...")

        system_prompt = (
            "You are an expert at identifying GitHub issues suitable for newcomers.\n\n"
            "For each issue, evaluate if it could be a 'good first issue' — meaning:\n"
            "- The scope is small enough for someone unfamiliar with the codebase\n"
            "- It doesn't require deep architectural knowledge\n"
            "- It has a clear, achievable goal\n"
            "- A beginner could complete it with some guidance\n\n"
            "Rate each issue on a 1-5 scale:\n"
            "  1 = Not suitable (too complex, requires deep knowledge)\n"
            "  2 = Unlikely (mostly too complex but has some simple aspects)\n"
            "  3 = Maybe (could work with significant guidance)\n"
            "  4 = Good candidate (clear scope, moderate effort)\n"
            "  5 = Excellent candidate (small, clear, well-defined)\n\n"
            "Return ONLY valid JSON: an array of objects with fields:\n"
            '  {"index": int, "gfi_score": int, "reason": "1 sentence explanation"}\n'
            "Only include issues scoring 3 or above. No markdown, no extra text."
        )

        issue_lines: list[str] = []
        for i, issue in enumerate(batch):
            body_preview = issue.body[:400] if issue.body else "(no description)"
            labels_str = ", ".join(issue.labels) if issue.labels else "none"
            issue_lines.append(
                f"{i}. [{issue.repo}] {issue.title}\n"
                f"   Labels: {labels_str} | Comments: {issue.comments}\n"
                f"   Body: {body_preview}"
            )

        user_prompt = (
            f"## Issues to evaluate ({len(batch)} total)\n\n"
            + "\n\n".join(issue_lines)
        )

        try:
            response = client.chat.completions.create(
                model=MODEL,
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt},
                ],
                max_completion_tokens=MAX_TOKENS,
            )

            content = response.choices[0].message.content.strip()
            if content.startswith("```"):
                content = content.split("\n", 1)[1].rsplit("```", 1)[0].strip()

            results = json.loads(content)

            # Remap indices to global positions
            for r in results:
                r["index"] = r["index"] + batch_start
            all_results.extend(results)

            usage = response.usage
            if usage:
                cost = (usage.prompt_tokens / 1_000_000) * 0.15 + (usage.completion_tokens / 1_000_000) * 0.60
                print(f"    Tokens: {usage.prompt_tokens} in / {usage.completion_tokens} out (~${cost:.4f})")

        except (json.JSONDecodeError, Exception) as exc:
            logger.warning("  Batch %d failed: %s", batch_num, exc)

    return all_results


# ---------------------------------------------------------------------------
# LLM: Generate improved descriptions
# ---------------------------------------------------------------------------


def improve_descriptions(issues: list[RawIssue], gfi_results: list[dict], api_key: str) -> dict[int, str]:
    """Use LLM to generate improved, beginner-friendly descriptions for identified GFIs.

    Returns dict mapping issue index -> improved description.
    """
    from openai import OpenAI
    client = OpenAI(api_key=api_key)

    improvements: dict[int, str] = {}

    # Only improve issues scoring 4+
    high_scoring = [r for r in gfi_results if r.get("gfi_score", 0) >= 4]

    if not high_scoring:
        high_scoring = gfi_results[:DEFAULT_TOP_N]

    for batch_start in range(0, len(high_scoring), 5):
        batch = high_scoring[batch_start:batch_start + 5]
        batch_num = batch_start // 5 + 1
        total_batches = (len(high_scoring) + 4) // 5

        print(f"  ✍️  Improving descriptions batch {batch_num}/{total_batches}...")

        system_prompt = (
            "You are a maintainer making GitHub issues beginner-friendly.\n\n"
            "For each issue, write an improved description that would make it an excellent "
            "'good first issue'. The improved description should include:\n\n"
            "1. **Context**: Brief explanation of what the project/component does\n"
            "2. **Problem**: What needs to be fixed/added (clear and specific)\n"
            "3. **Suggested approach**: Step-by-step guidance on how to solve it\n"
            "4. **Files to look at**: Specific files or directories to start with\n"
            "5. **Acceptance criteria**: Clear definition of done\n"
            "6. **Resources**: Links to relevant docs or similar PRs if applicable\n\n"
            "Keep the description concise but complete. Use markdown formatting.\n\n"
            "Return ONLY valid JSON: an array of objects with fields:\n"
            '  {"index": int, "improved_description": "markdown text"}\n'
            "No extra text outside the JSON."
        )

        issue_lines: list[str] = []
        for i, result in enumerate(batch):
            idx = result["index"]
            issue = issues[idx]
            issue_lines.append(
                f"{i}. [{issue.repo}] {issue.title}\n"
                f"   Labels: {', '.join(issue.labels)}\n"
                f"   Original body:\n{issue.body[:600] if issue.body else '(empty)'}\n"
                f"   Why it's a GFI: {result.get('reason', '')}"
            )

        user_prompt = (
            f"## Issues to improve ({len(batch)} total)\n\n"
            + "\n\n---\n\n".join(issue_lines)
        )

        try:
            response = client.chat.completions.create(
                model=MODEL,
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt},
                ],
                max_completion_tokens=MAX_TOKENS * 2,
            )

            content = response.choices[0].message.content.strip()
            if content.startswith("```"):
                content = content.split("\n", 1)[1].rsplit("```", 1)[0].strip()

            results = json.loads(content)

            for r in results:
                local_idx = r["index"]
                if 0 <= local_idx < len(batch):
                    global_idx = batch[local_idx]["index"]
                    improvements[global_idx] = r["improved_description"]

            usage = response.usage
            if usage:
                cost = (usage.prompt_tokens / 1_000_000) * 0.15 + (usage.completion_tokens / 1_000_000) * 0.60
                print(f"    Tokens: {usage.prompt_tokens} in / {usage.completion_tokens} out (~${cost:.4f})")

        except (json.JSONDecodeError, Exception) as exc:
            logger.warning("  Description improvement batch %d failed: %s", batch_num, exc)

    return improvements


# ---------------------------------------------------------------------------
# CSV export
# ---------------------------------------------------------------------------


def export_results(discovered: list[DiscoveredGFI], output_path: Path) -> None:
    """Export discovered GFIs to CSV."""
    fieldnames = [
        "gfi_score", "title", "url", "repo", "labels", "comments",
        "created_at", "reason", "improved_description", "original_body_excerpt",
    ]

    sorted_gfis = sorted(discovered, key=lambda g: g.gfi_score, reverse=True)

    with output_path.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=fieldnames)
        writer.writeheader()
        for gfi in sorted_gfis:
            writer.writerow({
                "gfi_score": gfi.gfi_score,
                "title": gfi.title,
                "url": gfi.url,
                "repo": gfi.repo,
                "labels": gfi.labels,
                "comments": gfi.comments,
                "created_at": gfi.created_at[:10] if gfi.created_at else "",
                "reason": gfi.reason,
                "improved_description": gfi.improved_description,
                "original_body_excerpt": gfi.original_body_excerpt,
            })


# ---------------------------------------------------------------------------
# Repo loading (reuse from scraper)
# ---------------------------------------------------------------------------


def load_repos_file(path: Path) -> list[str]:
    """Load repo names from a file (one per line, # comments ignored)."""
    repos: list[str] = []
    with path.open("r", encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            repos.append(line)
    return repos


def normalize_repos(repos: list[str], org: str) -> list[str]:
    """Normalize repo names to owner/repo format."""
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


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Discover potential good first issues from all open issues using LLM.",
    )
    parser.add_argument(
        "--org",
        default="canonical",
        help="GitHub organization (default: canonical)",
    )
    parser.add_argument(
        "--repos",
        help="Comma-separated list of repo names to search",
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
        "--top", "-n",
        type=int,
        default=DEFAULT_TOP_N,
        help=f"Max issues to process with LLM (default: {DEFAULT_TOP_N})",
    )
    parser.add_argument(
        "--no-improve",
        action="store_true",
        help="Skip generating improved descriptions (faster, cheaper)",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()

    # Validate API key
    api_key = os.environ.get("OPENAI_API_KEY", "")
    if not api_key:
        print("❌ Error: OPENAI_API_KEY environment variable not set.", file=sys.stderr)
        return 1

    # Resolve repos
    repos: list[str] = []
    if args.repos:
        repos.extend(r.strip() for r in args.repos.split(",") if r.strip())
    if args.repos_file:
        repos_path = Path(args.repos_file)
        if not repos_path.exists():
            print(f"❌ Error: Repos file not found: {repos_path}", file=sys.stderr)
            return 1
        repos.extend(load_repos_file(repos_path))

    if not repos:
        print("❌ Error: Specify repos with --repos or --repos-file", file=sys.stderr)
        return 1

    repos = normalize_repos(repos, args.org)
    print(f"🔍 Scanning {len(repos)} repos for potential GFIs...\n")

    # Step 1: Fetch all open issues
    all_issues = fetch_all_issues(repos)
    if not all_issues:
        print("❌ No open issues found.")
        return 0

    # Limit to top N for LLM processing (prioritize by fewer comments = less contested)
    all_issues.sort(key=lambda i: i.comments)
    issues_to_evaluate = all_issues[:args.top]

    print(f"\n📊 Evaluating {len(issues_to_evaluate)} issues with LLM...")

    # Step 2: Identify potential GFIs
    gfi_results = identify_gfis(issues_to_evaluate, api_key)

    if not gfi_results:
        print("❌ LLM did not identify any potential GFIs.")
        return 0

    print(f"\n✅ Found {len(gfi_results)} potential GFIs (score ≥ 3)")

    # Step 3: Generate improved descriptions
    improvements: dict[int, str] = {}
    if not args.no_improve:
        print(f"\n✍️  Generating improved descriptions...")
        improvements = improve_descriptions(issues_to_evaluate, gfi_results, api_key)
        print(f"   Generated {len(improvements)} improved descriptions")

    # Step 4: Build output
    discovered: list[DiscoveredGFI] = []
    for result in gfi_results:
        idx = result["index"]
        if idx >= len(issues_to_evaluate):
            continue
        issue = issues_to_evaluate[idx]
        discovered.append(DiscoveredGFI(
            title=issue.title,
            url=issue.url,
            repo=issue.repo,
            number=issue.number,
            labels="; ".join(issue.labels),
            comments=issue.comments,
            created_at=issue.created_at,
            gfi_score=result.get("gfi_score", 3),
            reason=result.get("reason", ""),
            improved_description=improvements.get(idx, ""),
            original_body_excerpt=issue.body[:200] if issue.body else "",
        ))

    # Export
    output_path = Path(args.output)
    export_results(discovered, output_path)

    # Print summary
    print(f"\n{'='*70}")
    print(f"  🎯 Discovered {len(discovered)} Potential Good First Issues")
    print(f"{'='*70}")

    for i, gfi in enumerate(sorted(discovered, key=lambda g: g.gfi_score, reverse=True)[:10], 1):
        score_stars = "⭐" * gfi.gfi_score
        print(f"\n  {i}. [{score_stars}] {gfi.title}")
        print(f"     {gfi.url}")
        print(f"     Why: {gfi.reason}")
        if gfi.improved_description:
            preview = gfi.improved_description[:100].replace("\n", " ")
            print(f"     Improved: {preview}...")

    print(f"\n📁 Full results: {output_path}")
    if improvements:
        print("💡 Tip: Use improved descriptions to update issues on GitHub")

    return 0


if __name__ == "__main__":
    sys.exit(main())
