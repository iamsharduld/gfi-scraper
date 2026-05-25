#!/usr/bin/env python3
"""Assess the quality of 'good first issue' issues using heuristics and LLM.

Heuristic quality signals evaluate whether an issue is truly suitable for
newcomers: clear description, well-defined scope, mentoring signals, and
actionability.

Optional LLM assessment provides deeper evaluation via a single API call.

Usage:
    gfi-assess [--csv FILE] [--llm] [--top N] [--output FILE]
"""

from __future__ import annotations

import argparse
import csv
import json
import logging
import os
import re
import subprocess
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

DEFAULT_CSV = "good_first_issues.csv"
DEFAULT_OUTPUT = "good_first_issues_assessed.csv"
DEFAULT_TOP_N = 50
MODEL = "gpt-5.5"
MAX_TOKENS = 4000

# LLM assessment cache
CACHE_DIR = Path(".cache")
LLM_CACHE_FILE = CACHE_DIR / "quality_llm_cache.json"

# ---------------------------------------------------------------------------
# Heuristic quality scoring
# ---------------------------------------------------------------------------


@dataclass
class QualityScores:
    """Quality assessment sub-scores (0-100 each)."""

    description_score: float = 0.0
    scope_score: float = 0.0
    mentoring_score: float = 0.0
    actionability_score: float = 0.0
    overall_score: float = 0.0


def assess_description_quality(body: str) -> float:
    """Score how well-written the issue description is (0-100)."""
    if not body:
        return 10.0

    score = 0.0

    # Length (longer is generally better for GFIs, up to a point)
    length = len(body)
    if length > 500:
        score += 30.0
    elif length > 200:
        score += 20.0
    elif length > 50:
        score += 10.0

    # Has markdown headings (structured description)
    if re.search(r"^#{1,3}\s+", body, re.MULTILINE):
        score += 20.0

    # Has code blocks (shows technical context)
    if "```" in body:
        score += 15.0

    # Has bullet points or numbered lists
    if re.search(r"^[\s]*[-*•]\s+", body, re.MULTILINE) or re.search(r"^[\s]*\d+[.)]\s+", body, re.MULTILINE):
        score += 15.0

    # Has links (references to docs, code, related issues)
    if re.search(r"https?://", body) or re.search(r"\[.*?\]\(.*?\)", body):
        score += 10.0

    # Has task checkboxes
    if re.search(r"- \[[ x]\]", body):
        score += 10.0

    return min(100.0, score)


def assess_scope_clarity(title: str, body: str, labels: list[str]) -> float:
    """Score how clearly defined the scope is (0-100)."""
    score = 0.0

    # Title specificity (not too short, not too generic)
    title_words = title.split()
    if len(title_words) >= 4:
        score += 20.0
    elif len(title_words) >= 2:
        score += 10.0

    # Title doesn't start with vague words
    vague_starters = {"fix", "bug", "issue", "problem", "update", "change"}
    if title_words and title_words[0].lower() not in vague_starters:
        score += 10.0

    # Body mentions specific files or paths
    if body and re.search(r"[\w/]+\.\w{1,5}", body):
        score += 20.0

    # Body mentions specific functions/methods
    if body and re.search(r"`[a-zA-Z_]\w*(\(\))?`", body):
        score += 15.0

    # Has focused labels (more specific labels = clearer scope)
    non_gfi_labels = [l for l in labels if l.lower() not in ("good first issue", "good-first-issue")]
    if len(non_gfi_labels) >= 1:
        score += 15.0
    if len(non_gfi_labels) >= 2:
        score += 10.0

    # Body is not excessively long (scope creep signal)
    if body and len(body) > 3000:
        score -= 10.0

    return max(0.0, min(100.0, score))


def assess_mentoring_signals(body: str, labels: list[str]) -> float:
    """Score presence of mentoring/guidance for newcomers (0-100)."""
    score = 0.0

    body_lower = (body or "").lower()

    # Mentoring keywords
    mentoring_keywords = [
        "mentor", "help", "beginner", "newcomer", "first time",
        "getting started", "happy to help", "feel free to ask",
        "pair", "guide", "walkthrough",
    ]
    for keyword in mentoring_keywords:
        if keyword in body_lower:
            score += 15.0
            break

    # Links to contributing guide or docs
    if re.search(r"contributing|CONTRIBUTING", body or ""):
        score += 20.0

    # Links to related docs/guides
    doc_patterns = [r"docs?/", r"wiki", r"readme", r"guide", r"tutorial"]
    for pattern in doc_patterns:
        if re.search(pattern, body_lower):
            score += 10.0
            break

    # Has hints about where to look
    location_hints = ["look at", "start with", "check out", "see file", "in the file", "located in"]
    for hint in location_hints:
        if hint in body_lower:
            score += 20.0
            break

    # Labels that suggest mentoring
    mentoring_labels = {"mentored", "help wanted", "easy", "starter", "beginner-friendly"}
    if any(l.lower() in mentoring_labels for l in labels):
        score += 15.0

    # Has contact/communication info
    if re.search(r"@\w+|slack|discord|matrix|irc", body_lower):
        score += 10.0

    return min(100.0, score)


def assess_actionability(body: str) -> float:
    """Score how actionable the issue is for a newcomer (0-100)."""
    score = 0.0

    body_lower = (body or "").lower()

    # Has steps to reproduce or implementation steps
    step_patterns = [
        r"steps?\s*(to|:)", r"how to reproduce", r"reproduction",
        r"expected\s*(behavior|result|output)",
        r"actual\s*(behavior|result|output)",
    ]
    for pattern in step_patterns:
        if re.search(pattern, body_lower):
            score += 25.0
            break

    # Has acceptance criteria
    acceptance_patterns = [
        r"acceptance\s*criteria", r"definition\s*of\s*done",
        r"done\s*when", r"complete\s*when", r"should\s*(be|have|return|display)",
    ]
    for pattern in acceptance_patterns:
        if re.search(pattern, body_lower):
            score += 25.0
            break

    # Has task checkboxes (clear list of work)
    if body and re.search(r"- \[[ x]\]", body):
        score += 20.0

    # Has clear before/after or current/desired state
    if re.search(r"(current|before|now).*?(desired|after|should|expected)", body_lower):
        score += 15.0

    # Has testing hints
    if re.search(r"test|verify|validate|assert", body_lower):
        score += 15.0

    return min(100.0, score)


def compute_quality_scores(
    title: str,
    body: str,
    labels: list[str],
) -> QualityScores:
    """Compute all quality heuristic scores for an issue."""
    desc = assess_description_quality(body)
    scope = assess_scope_clarity(title, body, labels)
    mentoring = assess_mentoring_signals(body, labels)
    actionability = assess_actionability(body)

    overall = (desc * 0.30 + scope * 0.25 + mentoring * 0.20 + actionability * 0.25)

    return QualityScores(
        description_score=desc,
        scope_score=scope,
        mentoring_score=mentoring,
        actionability_score=actionability,
        overall_score=overall,
    )


# ---------------------------------------------------------------------------
# LLM quality assessment
# ---------------------------------------------------------------------------


def load_llm_cache() -> dict:
    """Load LLM assessment cache."""
    if LLM_CACHE_FILE.exists():
        try:
            return json.loads(LLM_CACHE_FILE.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            pass
    return {}


def save_llm_cache(cache: dict) -> None:
    """Save LLM assessment cache."""
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    LLM_CACHE_FILE.write_text(json.dumps(cache, indent=2), encoding="utf-8")


def fetch_issue_body(url: str) -> str:
    """Fetch the full body of an issue from GitHub using gh CLI."""
    # URL format: https://github.com/owner/repo/issues/123
    parts = url.rstrip("/").split("/")
    if len(parts) < 5:
        return ""
    owner = parts[-4]
    repo = parts[-3]
    number = parts[-1]

    try:
        result = subprocess.run(
            ["gh", "issue", "view", number, "--repo", f"{owner}/{repo}", "--json", "body"],
            capture_output=True, text=True, check=True, timeout=30,
        )
        data = json.loads(result.stdout)
        return data.get("body", "")
    except (subprocess.CalledProcessError, json.JSONDecodeError, subprocess.TimeoutExpired) as exc:
        logger.warning("Could not fetch body for %s: %s", url, exc)
        return ""


def assess_with_llm(issues: list[dict], api_key: str) -> list[dict]:
    """Use LLM to assess quality of issues.

    Args:
        issues: List of dicts with keys: url, title, body, labels
        api_key: OpenAI API key

    Returns:
        List of dicts with keys: url, llm_score, llm_clarity, llm_scope, llm_actionability, llm_reason
    """
    from openai import OpenAI

    client = OpenAI(api_key=api_key)

    system_prompt = (
        "You are an expert at evaluating GitHub 'good first issue' quality. "
        "For each issue, rate how suitable it is for a newcomer to open source.\n\n"
        "Rate each issue on three dimensions (1-5 scale):\n"
        "- clarity: How clear is the problem description? (1=vague, 5=crystal clear)\n"
        "- scope: Is the scope appropriate for a beginner? (1=too complex/vague, 5=perfectly scoped)\n"
        "- actionability: Can someone start working immediately? (1=no guidance, 5=step-by-step)\n\n"
        "Return ONLY valid JSON: an array of objects with fields:\n"
        '  {"index": int, "clarity": int, "scope": int, "actionability": int, "reason": "1 sentence"}\n'
        "No markdown, no extra text."
    )

    issue_lines: list[str] = []
    for i, issue in enumerate(issues):
        body_preview = (issue.get("body") or "")[:500]
        labels = issue.get("labels", "")
        issue_lines.append(
            f"{i}. [{issue['title']}] Labels: {labels}\n"
            f"   Body: {body_preview}"
        )

    user_prompt = (
        f"## Issues to evaluate ({len(issues)} total)\n\n"
        + "\n\n".join(issue_lines)
    )

    print(f"  ⏳ Assessing {len(issues)} issues with LLM...")

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

    # Report cost
    usage = response.usage
    if usage:
        input_cost = (usage.prompt_tokens / 1_000_000) * 0.15
        output_cost = (usage.completion_tokens / 1_000_000) * 0.60
        total_cost = input_cost + output_cost
        print(f"  Tokens: {usage.prompt_tokens} in / {usage.completion_tokens} out")
        print(f"  Cost: ~${total_cost:.4f}")

    # Map results back to issues
    assessed: list[dict] = []
    for result in results:
        idx = result.get("index", -1)
        if 0 <= idx < len(issues):
            clarity = min(5, max(1, result.get("clarity", 3)))
            scope = min(5, max(1, result.get("scope", 3)))
            actionability = min(5, max(1, result.get("actionability", 3)))
            llm_score = ((clarity + scope + actionability) / 15.0) * 100

            assessed.append({
                "url": issues[idx]["url"],
                "llm_score": round(llm_score, 1),
                "llm_clarity": clarity,
                "llm_scope": scope,
                "llm_actionability": actionability,
                "llm_reason": result.get("reason", ""),
            })

    return assessed


# ---------------------------------------------------------------------------
# CLI: gfi-assess
# ---------------------------------------------------------------------------


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Assess quality of good first issues using heuristics and optionally LLM.",
    )
    parser.add_argument(
        "--csv",
        default=DEFAULT_CSV,
        help=f"Path to scraped issues CSV (default: {DEFAULT_CSV})",
    )
    parser.add_argument(
        "--output", "-o",
        default=DEFAULT_OUTPUT,
        help=f"Output CSV with quality scores (default: {DEFAULT_OUTPUT})",
    )
    parser.add_argument(
        "--llm",
        action="store_true",
        help="Enable LLM-based quality assessment (requires OPENAI_API_KEY)",
    )
    parser.add_argument(
        "--top", "-n",
        type=int,
        default=DEFAULT_TOP_N,
        help=f"Number of issues to assess with LLM (default: {DEFAULT_TOP_N})",
    )
    parser.add_argument(
        "--no-refetch",
        action="store_true",
        help="Skip refetching full issue bodies (use CSV excerpts only)",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    csv_path = Path(args.csv)

    if not csv_path.exists():
        print(f"❌ Error: CSV not found at '{csv_path}'.", file=sys.stderr)
        print("   Run gfi-scrape first.", file=sys.stderr)
        return 1

    # Load issues from CSV
    issues: list[dict] = []
    with csv_path.open("r", encoding="utf-8") as fh:
        reader = csv.DictReader(fh)
        for row in reader:
            issues.append(dict(row))

    if not issues:
        print("❌ No issues found in CSV.", file=sys.stderr)
        return 1

    print(f"📋 Loaded {len(issues)} issues from {csv_path}")

    # Fetch full bodies if needed for better assessment
    if not args.no_refetch:
        print("🔄 Fetching full issue bodies for quality assessment...")
        for i, issue in enumerate(issues):
            body = fetch_issue_body(issue["url"])
            if body:
                issue["_full_body"] = body
            if (i + 1) % 10 == 0:
                print(f"   Fetched {i + 1}/{len(issues)}...")
        print(f"   ✓ Fetched bodies for {sum(1 for i in issues if '_full_body' in i)} issues")

    # Compute heuristic quality scores
    print("📊 Computing heuristic quality scores...")
    for issue in issues:
        body = issue.get("_full_body", issue.get("body_excerpt", ""))
        labels = [l.strip() for l in issue.get("labels", "").split(";") if l.strip()]

        scores = compute_quality_scores(issue["title"], body, labels)
        issue["quality_score"] = f"{scores.overall_score:.1f}"
        issue["quality_description"] = f"{scores.description_score:.1f}"
        issue["quality_scope"] = f"{scores.scope_score:.1f}"
        issue["quality_mentoring"] = f"{scores.mentoring_score:.1f}"
        issue["quality_actionability"] = f"{scores.actionability_score:.1f}"

    # Optional LLM assessment
    if args.llm:
        api_key = os.environ.get("OPENAI_API_KEY", "")
        if not api_key:
            print("❌ Error: OPENAI_API_KEY not set for --llm mode.", file=sys.stderr)
            return 1

        # Assess top N issues by existing rank
        top_issues = issues[:args.top]

        # Prepare for LLM (use full body if available)
        llm_input = []
        for issue in top_issues:
            llm_input.append({
                "url": issue["url"],
                "title": issue["title"],
                "body": issue.get("_full_body", issue.get("body_excerpt", "")),
                "labels": issue.get("labels", ""),
            })

        # Check cache
        cache = load_llm_cache()
        uncached = [i for i in llm_input if i["url"] not in cache]

        if uncached:
            try:
                # Batch in groups of 20 to stay within token limits
                batch_size = 20
                for batch_start in range(0, len(uncached), batch_size):
                    batch = uncached[batch_start:batch_start + batch_size]
                    results = assess_with_llm(batch, api_key)
                    for result in results:
                        cache[result["url"]] = result
                save_llm_cache(cache)
            except Exception as exc:
                print(f"⚠️  LLM assessment failed: {exc}", file=sys.stderr)
        else:
            print("  ✓ All issues found in LLM cache")

        # Merge LLM results into issues
        for issue in issues:
            if issue["url"] in cache:
                cached = cache[issue["url"]]
                issue["llm_quality_score"] = str(cached.get("llm_score", ""))
                issue["llm_quality_reason"] = cached.get("llm_reason", "")

    # Remove internal fields before export
    for issue in issues:
        issue.pop("_full_body", None)

    # Export enriched CSV
    output_path = Path(args.output)
    fieldnames = list(issues[0].keys()) if issues else []
    with output_path.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(issues)

    print(f"\n✅ Quality assessment complete → {output_path}")

    # Print summary
    scored = [(issue["title"], float(issue["quality_score"]), issue["url"]) for issue in issues]
    scored.sort(key=lambda x: x[1], reverse=True)

    print(f"\n{'='*70}")
    print(f"  🏆 Top 10 Highest Quality GFIs")
    print(f"{'='*70}")
    for i, (title, score, url) in enumerate(scored[:10], 1):
        print(f"  {i:2d}. [{score:.1f}] {title[:60]}")
        print(f"      {url}")

    print(f"\n{'='*70}")
    print(f"  ⚠️  Bottom 5 Lowest Quality GFIs (need improvement)")
    print(f"{'='*70}")
    for i, (title, score, url) in enumerate(scored[-5:], 1):
        print(f"  {i:2d}. [{score:.1f}] {title[:60]}")
        print(f"      {url}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
