#!/usr/bin/env python3
"""Interactive TUI for discovering and matching 'good first issues' from Canonical.

A beautiful terminal interface built with Rich that lets you:
- Browse all scraped issues in a formatted table
- Filter by repo, labels, or keywords
- Run LLM-powered matching against your developer profile
- View detailed issue information

Usage:
    python3 tui.py [--csv FILE]
"""

from __future__ import annotations

import argparse
import csv
import json
import os
import sys
from dataclasses import dataclass
from pathlib import Path

from rich.console import Console
from rich.layout import Layout
from rich.panel import Panel
from rich.prompt import Confirm, IntPrompt, Prompt
from rich.table import Table
from rich.text import Text
from rich.theme import Theme
from rich.columns import Columns
from rich.markdown import Markdown
from rich.rule import Rule
from rich import box

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

DEFAULT_CSV = "good_first_issues.csv"
MODEL = "gpt-5.5"
MAX_TOKENS = 2000

custom_theme = Theme({
    "title": "bold cyan",
    "subtitle": "dim cyan",
    "success": "bold green",
    "warning": "bold yellow",
    "error": "bold red",
    "info": "bold blue",
    "muted": "dim white",
    "highlight": "bold magenta",
    "score.high": "bold green",
    "score.mid": "yellow",
    "score.low": "red",
})

console = Console(theme=custom_theme)

# ---------------------------------------------------------------------------
# Data model
# ---------------------------------------------------------------------------


@dataclass
class Issue:
    """Issue loaded from CSV."""

    rank: int
    total_score: float
    title: str
    url: str
    repo: str
    stars: int
    created_at: str
    comments: int
    assignees: str
    labels: str
    freshness_score: float
    competition_score: float
    availability_score: float
    popularity_score: float
    activity_score: float
    linked_pr_state: str = ""
    body_excerpt: str = ""
    is_new: bool = False
    score_delta: float = 0.0
    pr_score: float = 0.0


# ---------------------------------------------------------------------------
# Data loading
# ---------------------------------------------------------------------------


def load_issues(csv_path: Path) -> list[Issue]:
    """Load issues from CSV."""
    issues: list[Issue] = []
    with csv_path.open("r", encoding="utf-8") as fh:
        reader = csv.DictReader(fh)
        for row in reader:
            issues.append(Issue(
                rank=int(row["rank"]),
                total_score=float(row["total_score"]),
                title=row["title"],
                url=row["url"],
                repo=row["repo"],
                stars=int(row["stars"]),
                created_at=row["created_at"],
                comments=int(row["comments"]),
                assignees=row["assignees"],
                labels=row["labels"],
                freshness_score=float(row["freshness_score"]),
                competition_score=float(row["competition_score"]),
                availability_score=float(row["availability_score"]),
                popularity_score=float(row["popularity_score"]),
                activity_score=float(row["activity_score"]),
                linked_pr_state=row.get("linked_pr_state", ""),
                body_excerpt=row.get("body_excerpt", ""),
                is_new=row.get("is_new", "") == "🆕",
                score_delta=float(row["score_delta"]) if row.get("score_delta") else 0.0,
                pr_score=float(row["pr_score"]) if row.get("pr_score") else 0.0,
            ))
    return issues


# ---------------------------------------------------------------------------
# Display helpers
# ---------------------------------------------------------------------------


def score_style(score: float) -> str:
    """Return style based on score value."""
    if score >= 75:
        return "score.high"
    elif score >= 50:
        return "score.mid"
    return "score.low"


def render_score_bar(score: float, width: int = 15) -> Text:
    """Render a visual score bar."""
    filled = int((score / 100) * width)
    bar = Text()
    bar.append("█" * filled, style=score_style(score))
    bar.append("░" * (width - filled), style="muted")
    bar.append(f" {score:.1f}", style=score_style(score))
    return bar


def truncate(text: str, max_len: int = 50) -> str:
    """Truncate text with ellipsis."""
    return text[:max_len - 1] + "…" if len(text) > max_len else text


# ---------------------------------------------------------------------------
# UI Screens
# ---------------------------------------------------------------------------


def show_header() -> None:
    """Display the app header."""
    header_text = Text()
    header_text.append("🎯 ", style="bold")
    header_text.append("Good First Issue Finder", style="bold cyan")
    header_text.append(" — Canonical GitHub Repos", style="dim cyan")

    console.print()
    console.print(Panel(
        header_text,
        border_style="cyan",
        padding=(0, 2),
    ))


def show_stats(issues: list[Issue]) -> None:
    """Display quick stats panel."""
    repos = len(set(i.repo for i in issues))
    avg_score = sum(i.total_score for i in issues) / len(issues) if issues else 0
    unassigned = sum(1 for i in issues if not i.assignees)
    new_count = sum(1 for i in issues if i.is_new)
    with_pr = sum(1 for i in issues if i.linked_pr_state)

    stats = Table.grid(padding=(0, 3))
    stats.add_row(
        Text(f"📋 {len(issues)}", style="bold"), "issues",
        Text(f"📦 {repos}", style="bold"), "repos",
        Text(f"📊 {avg_score:.1f}", style="bold"), "avg score",
        Text(f"✅ {unassigned}", style="bold"), "unassigned",
    )
    stats.add_row(
        Text(f"🆕 {new_count}", style="bold green"), "new",
        Text(f"🔗 {with_pr}", style="bold yellow"), "with PRs",
        Text("", style="dim"), "",
        Text("", style="dim"), "",
    )

    console.print(Panel(stats, title="[subtitle]Overview[/]", border_style="dim"))


def show_issues_table(issues: list[Issue], page: int = 1, page_size: int = 15) -> int:
    """Display paginated issues table. Returns total pages."""
    total_pages = (len(issues) + page_size - 1) // page_size
    start = (page - 1) * page_size
    end = start + page_size
    page_issues = issues[start:end]

    table = Table(
        title=f"[title]Issues[/] [muted](page {page}/{total_pages})[/]",
        box=box.ROUNDED,
        border_style="blue",
        header_style="bold blue",
        show_lines=False,
        padding=(0, 1),
    )

    table.add_column("#", style="dim", width=4, justify="right")
    table.add_column("Score", width=20)
    table.add_column("Title", min_width=30, max_width=45)
    table.add_column("Repository", style="cyan", max_width=25)
    table.add_column("⭐", justify="right", width=6)
    table.add_column("💬", justify="right", width=4)
    table.add_column("Status", width=8)
    table.add_column("📅", width=12)

    for issue in page_issues:
        # Build status badges
        status = Text()
        if issue.is_new:
            status.append("🆕", style="bold green")
        if issue.linked_pr_state == "OPEN":
            status.append("⚠️", style="bold yellow")
        elif issue.linked_pr_state == "MERGED":
            status.append("✓PR", style="dim")

        table.add_row(
            str(issue.rank),
            render_score_bar(issue.total_score),
            truncate(issue.title, 43),
            issue.repo.replace("canonical/", ""),
            str(issue.stars),
            str(issue.comments),
            status,
            issue.created_at,
        )

    console.print(table)
    return total_pages


def show_issue_detail(issue: Issue) -> None:
    """Show detailed view of a single issue."""
    console.print()
    console.print(Rule(f"[title]Issue #{issue.rank}[/]"))

    # Title and URL
    title_text = Text()
    if issue.is_new:
        title_text.append("🆕 ", style="bold green")
    title_text.append(issue.title, style="bold")
    console.print(f"\n  ", end="")
    console.print(title_text)
    console.print(f"  [link={issue.url}]{issue.url}[/link]\n", style="muted")

    # Metadata grid
    meta = Table.grid(padding=(0, 2))
    meta.add_column(style="dim", width=14)
    meta.add_column()
    meta.add_row("Repository:", f"[cyan]{issue.repo}[/]")
    meta.add_row("Stars:", f"⭐ {issue.stars}")
    meta.add_row("Comments:", f"💬 {issue.comments}")
    meta.add_row("Created:", f"📅 {issue.created_at}")
    meta.add_row("Assignees:", issue.assignees or "[success]None (available!)[/]")
    meta.add_row("Labels:", issue.labels)

    # PR status
    if issue.linked_pr_state:
        pr_style = {"OPEN": "bold yellow", "MERGED": "dim", "CLOSED": "dim green"}
        pr_text = {
            "OPEN": "⚠️  OPEN PR (active competition!)",
            "MERGED": "✓ PR merged (may be resolved)",
            "CLOSED": "PR closed (abandoned attempt)",
        }
        meta.add_row(
            "Linked PR:",
            f"[{pr_style.get(issue.linked_pr_state, 'dim')}]"
            f"{pr_text.get(issue.linked_pr_state, issue.linked_pr_state)}[/]",
        )
    else:
        meta.add_row("Linked PR:", "[success]None (no competition)[/]")

    console.print(Panel(meta, border_style="dim", title="[subtitle]Details[/]"))

    # Body excerpt
    if issue.body_excerpt:
        console.print(Panel(
            issue.body_excerpt,
            title="[subtitle]Description[/]",
            border_style="dim",
            padding=(0, 2),
        ))

    # Score breakdown
    scores = Table(box=box.SIMPLE, border_style="dim", header_style="bold")
    scores.add_column("Component", width=16)
    scores.add_column("Score", width=22)
    scores.add_column("Weight")

    components = [
        ("Freshness", issue.freshness_score, "25%"),
        ("Competition", issue.competition_score, "25%"),
        ("Availability", issue.availability_score, "20%"),
        ("Popularity", issue.popularity_score, "15%"),
        ("Activity", issue.activity_score, "10%"),
        ("PR Status", issue.pr_score, "5%"),
    ]
    for name, score, weight in components:
        scores.add_row(name, render_score_bar(score, 12), weight)

    scores.add_row("", "", "")
    total_bar = render_score_bar(issue.total_score, 12)
    scores.add_row("[bold]TOTAL[/]", total_bar, "[bold]100%[/]")

    console.print(Panel(scores, border_style="dim", title="[subtitle]Score Breakdown[/]"))


def show_match_results(matches: list[dict], issues: list[Issue]) -> None:
    """Display LLM match results beautifully."""
    console.print()
    console.print(Rule("[success]🎯 Your Personalized Matches[/]"))
    console.print()

    issue_map = {i.rank: i for i in issues}

    for rank, match in enumerate(matches, 1):
        idx = match["issue_number"]
        reason = match.get("reason", "")
        issue = issue_map.get(idx)
        if not issue:
            # Try by index position
            if 1 <= idx <= len(issues):
                issue = issues[idx - 1]
        if not issue:
            continue

        # Match card
        style = "green" if rank <= 3 else "blue" if rank <= 7 else "dim"
        medal = {1: "🥇", 2: "🥈", 3: "🥉"}.get(rank, f" {rank}.")

        card_content = Text()
        card_content.append(f"{issue.title}\n", style="bold")
        card_content.append(f"{issue.repo}", style="cyan")
        card_content.append(f"  ⭐{issue.stars}  💬{issue.comments}  ", style="dim")
        card_content.append(f"Score: {issue.total_score:.1f}\n", style=score_style(issue.total_score))
        card_content.append(f"💡 {reason}\n", style="italic")
        card_content.append(issue.url, style="muted underline")

        console.print(Panel(
            card_content,
            title=f"[bold]{medal} Match #{rank}[/]",
            border_style=style,
            padding=(0, 2),
        ))


# ---------------------------------------------------------------------------
# LLM Matching
# ---------------------------------------------------------------------------


def gather_profile_interactive() -> dict:
    """Gather user profile with Rich prompts."""
    console.print()
    console.print(Rule("[title]👤 Developer Profile[/]"))
    console.print()

    languages = Prompt.ask(
        "  [bold]Programming languages[/] [muted](comma-separated)[/]",
        default="Python",
    )

    domains = Prompt.ask(
        "  [bold]Interests / domains[/] [muted](comma-separated)[/]",
        default="any",
    )

    console.print("\n  [bold]Experience level:[/]")
    console.print("    [dim]1.[/] Beginner  [dim]2.[/] Intermediate  [dim]3.[/] Advanced")
    exp_choice = Prompt.ask("  ", choices=["1", "2", "3"], default="1")
    exp_map = {"1": "beginner", "2": "intermediate", "3": "advanced"}

    extra = Prompt.ask(
        "\n  [bold]Anything else?[/] [muted](or Enter to skip)[/]",
        default="",
    )

    return {
        "languages": [l.strip() for l in languages.split(",")],
        "domains": [d.strip() for d in domains.split(",")],
        "experience": exp_map[exp_choice],
        "extra": extra,
    }


def run_llm_match(profile: dict, issues: list[Issue], top_n: int = 10) -> list[dict]:
    """Run LLM matching."""
    api_key = os.environ.get("OPENAI_API_KEY", "")
    if not api_key:
        console.print("[error]❌ OPENAI_API_KEY not set. Export it first.[/]")
        return []

    from openai import OpenAI

    # Build compressed issue list
    issue_lines = []
    for issue in issues:
        line = (
            f"{issue.rank}. [{issue.repo}] {issue.title} "
            f"| Labels: {issue.labels} | Score: {issue.total_score:.0f} "
            f"| Stars: {issue.stars} | Comments: {issue.comments}"
        )
        issue_lines.append(line)

    system_prompt = (
        "You are a developer-issue matcher. Given a developer's profile and a numbered "
        "list of open source issues, select the best matches. Consider:\n"
        "- Language/technology alignment with the issue's repo and labels\n"
        "- Domain alignment with the developer's interests\n"
        "- Difficulty appropriateness for their experience level\n"
        "- Issue quality (higher score = fresher, less contested)\n\n"
        "Return ONLY valid JSON: an array of objects with fields:\n"
        '  {"issue_number": int, "reason": "brief explanation (1 sentence)"}\n'
        "Ordered from best match to worst. No markdown, no extra text."
    )

    user_prompt = (
        f"## Developer Profile\n"
        f"- Languages: {', '.join(profile['languages'])}\n"
        f"- Interests: {', '.join(profile['domains'])}\n"
        f"- Experience: {profile['experience']}\n"
        f"- Notes: {profile['extra'] or 'none'}\n\n"
        f"## Issues ({len(issues)} total)\n\n"
        + "\n".join(issue_lines)
        + f"\n\nSelect the top {top_n} best-matching issues for this developer."
    )

    client = OpenAI(api_key=api_key)

    with console.status("[bold blue]🤖 Matching with GPT-5.5...[/]", spinner="dots"):
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

    matches = json.loads(content)

    usage = response.usage
    if usage:
        cost = (usage.prompt_tokens / 1_000_000) * 0.15 + (usage.completion_tokens / 1_000_000) * 0.60
        console.print(
            f"  [muted]Tokens: {usage.prompt_tokens} in / {usage.completion_tokens} out | "
            f"Cost: ~${cost:.4f}[/]"
        )

    return matches


# ---------------------------------------------------------------------------
# Filter
# ---------------------------------------------------------------------------


def filter_issues(issues: list[Issue], query: str) -> list[Issue]:
    """Filter issues by keyword across title, repo, and labels."""
    query_lower = query.lower()
    return [
        i for i in issues
        if query_lower in i.title.lower()
        or query_lower in i.repo.lower()
        or query_lower in i.labels.lower()
    ]


# ---------------------------------------------------------------------------
# Main menu loop
# ---------------------------------------------------------------------------


def show_menu() -> None:
    """Display the main menu."""
    console.print()
    menu = Table.grid(padding=(0, 2))
    menu.add_column(style="bold cyan", width=4, justify="right")
    menu.add_column()
    menu.add_row("b", "Browse all issues (paginated table)")
    menu.add_row("n", "What's new (recently added issues)")
    menu.add_row("f", "Filter issues by keyword")
    menu.add_row("d", "Detail view of a specific issue")
    menu.add_row("m", "Match issues to your profile (LLM)")
    menu.add_row("s", "Show stats")
    menu.add_row("q", "Quit")

    console.print(Panel(menu, title="[title]Menu[/]", border_style="cyan", padding=(1, 2)))


def main() -> int:
    from gfi_scraper.scrape_good_first_issues import run_scrape, DEFAULT_ORG

    parser = argparse.ArgumentParser()
    parser.add_argument("--csv", default=DEFAULT_CSV)
    parser.add_argument("--org", default=DEFAULT_ORG)
    args = parser.parse_args()

    csv_path = Path(args.csv)
    if not csv_path.exists():
        console.print("[warning]⚠️  No issue data found.[/]")
        console.print("[info]Scraping fresh issues...[/]\n")
        result = run_scrape(args.org, args.csv)
        if result != 0:
            return result
    else:
        if Confirm.ask("[bold]Rescrape issues before starting?[/]", default=False):
            console.print("[info]Scraping fresh issues...[/]\n")
            result = run_scrape(args.org, args.csv)
            if result != 0:
                return result

    if not csv_path.exists():
        console.print(f"[error]❌ CSV not found: {csv_path}[/]")
        return 1

    issues = load_issues(csv_path)
    current_issues = issues  # May be filtered
    page = 1

    show_header()
    show_stats(issues)
    show_menu()

    while True:
        console.print()
        choice = Prompt.ask("[bold]→[/]", default="b").strip().lower()

        if choice == "q":
            console.print("\n[muted]Goodbye! 👋[/]\n")
            break

        elif choice == "b":
            page = 1
            total_pages = show_issues_table(current_issues, page)
            while True:
                nav = Prompt.ask(
                    "[muted]  n=next  p=prev  number=go to page  q=back[/]",
                    default="n",
                ).strip().lower()
                if nav == "q":
                    break
                elif nav == "n" and page < total_pages:
                    page += 1
                elif nav == "p" and page > 1:
                    page -= 1
                elif nav.isdigit():
                    target = int(nav)
                    if 1 <= target <= total_pages:
                        page = target
                total_pages = show_issues_table(current_issues, page)

        elif choice == "n":
            new_issues = [i for i in issues if i.is_new]
            if new_issues:
                console.print(f"\n  [success]🆕 {len(new_issues)} new issues since last run:[/]\n")
                show_issues_table(new_issues, 1, page_size=20)
            else:
                console.print("\n  [muted]No new issues detected. Run the scraper again to diff.[/]")

        elif choice == "f":
            query = Prompt.ask("  [bold]Search keyword[/]")
            if query:
                current_issues = filter_issues(issues, query)
                console.print(f"  [success]Found {len(current_issues)} matching issues[/]")
                if current_issues:
                    show_issues_table(current_issues, 1)
            else:
                current_issues = issues
                console.print("  [muted]Filter cleared[/]")

        elif choice == "d":
            rank_num = IntPrompt.ask("  [bold]Issue rank #[/]")
            issue = next((i for i in issues if i.rank == rank_num), None)
            if issue:
                show_issue_detail(issue)
            else:
                console.print(f"  [error]Issue #{rank_num} not found[/]")

        elif choice == "m":
            profile = gather_profile_interactive()
            try:
                matches = run_llm_match(profile, issues)
                if matches:
                    show_match_results(matches, issues)
            except Exception as exc:
                console.print(f"  [error]❌ Matching failed: {exc}[/]")

        elif choice == "s":
            show_stats(issues)
            # Top repos
            from collections import Counter
            repo_counts = Counter(i.repo for i in issues)
            top_repos = Table(
                title="[subtitle]Top Repos by Issue Count[/]",
                box=box.SIMPLE,
                border_style="dim",
            )
            top_repos.add_column("Repository", style="cyan")
            top_repos.add_column("Issues", justify="right")
            top_repos.add_column("Stars", justify="right")
            for repo, count in repo_counts.most_common(10):
                stars = next(i.stars for i in issues if i.repo == repo)
                top_repos.add_row(repo.replace("canonical/", ""), str(count), f"⭐{stars}")
            console.print(top_repos)

        else:
            show_menu()

    return 0


if __name__ == "__main__":
    sys.exit(main())
