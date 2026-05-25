#!/usr/bin/env python3
"""Interactive TUI for browsing discovered potential good first issues.

A terminal interface built with Rich that lets you:
- Browse LLM-discovered potential GFIs in a formatted table
- Filter by repo, score, or keywords
- View improved descriptions for each issue
- Copy issue URLs for quick navigation

Usage:
    gfi-discover-ui [--csv FILE]
"""

from __future__ import annotations

import argparse
import csv
import sys
from dataclasses import dataclass
from pathlib import Path

from rich.console import Console
from rich.panel import Panel
from rich.prompt import IntPrompt, Prompt
from rich.table import Table
from rich.text import Text
from rich.markdown import Markdown
from rich.rule import Rule
from rich import box

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

DEFAULT_CSV = "output/discovered_gfis.csv"

custom_theme = {
    "title": "bold cyan",
    "subtitle": "dim cyan",
    "success": "bold green",
    "warning": "bold yellow",
    "error": "bold red",
    "info": "bold blue",
    "muted": "dim white",
    "highlight": "bold magenta",
}

console = Console()


# ---------------------------------------------------------------------------
# Data model
# ---------------------------------------------------------------------------


@dataclass
class DiscoveredIssue:
    """A discovered potential GFI loaded from CSV."""

    gfi_score: int
    title: str
    url: str
    repo: str
    labels: str
    comments: int
    created_at: str
    reason: str
    improved_description: str
    original_body_excerpt: str


# ---------------------------------------------------------------------------
# Data loading
# ---------------------------------------------------------------------------


def load_discovered(csv_path: Path) -> list[DiscoveredIssue]:
    """Load discovered GFIs from CSV."""
    issues: list[DiscoveredIssue] = []
    with csv_path.open("r", encoding="utf-8") as fh:
        reader = csv.DictReader(fh)
        for row in reader:
            issues.append(DiscoveredIssue(
                gfi_score=int(row.get("gfi_score", 3)),
                title=row.get("title", ""),
                url=row.get("url", ""),
                repo=row.get("repo", ""),
                labels=row.get("labels", ""),
                comments=int(row.get("comments", 0)),
                created_at=row.get("created_at", ""),
                reason=row.get("reason", ""),
                improved_description=row.get("improved_description", ""),
                original_body_excerpt=row.get("original_body_excerpt", ""),
            ))
    return issues


# ---------------------------------------------------------------------------
# Display helpers
# ---------------------------------------------------------------------------


def score_stars(score: int) -> Text:
    """Render score as colored stars."""
    text = Text()
    colors = {5: "bold green", 4: "green", 3: "yellow"}
    style = colors.get(score, "dim")
    text.append("⭐" * score, style=style)
    text.append("☆" * (5 - score), style="dim")
    return text


def truncate(text: str, max_len: int = 50) -> str:
    """Truncate text with ellipsis."""
    return text[:max_len - 1] + "…" if len(text) > max_len else text


# ---------------------------------------------------------------------------
# UI Screens
# ---------------------------------------------------------------------------


def show_header(total: int, repos: int) -> None:
    """Display the app header."""
    header = Text()
    header.append("🔍 ", style="bold")
    header.append("Discovered Good First Issues", style="bold cyan")
    header.append(f" — {total} issues from {repos} repos", style="dim cyan")
    console.print()
    console.print(Panel(header, border_style="cyan", padding=(0, 2)))


def show_stats(issues: list[DiscoveredIssue]) -> None:
    """Display summary statistics."""
    repos = len(set(i.repo for i in issues))
    by_score = {5: 0, 4: 0, 3: 0}
    for i in issues:
        if i.gfi_score in by_score:
            by_score[i.gfi_score] += 1

    with_desc = sum(1 for i in issues if i.improved_description)

    stats = Table.grid(padding=(0, 3))
    stats.add_row(
        Text(f"⭐⭐⭐⭐⭐ {by_score[5]}", style="bold green"), "",
        Text(f"⭐⭐⭐⭐ {by_score[4]}", style="green"), "",
        Text(f"⭐⭐⭐ {by_score[3]}", style="yellow"), "",
    )
    stats.add_row(
        Text(f"📦 {repos} repos", style="bold"), "",
        Text(f"✍️  {with_desc} improved", style="bold"), "",
        Text("", style="dim"), "",
    )
    console.print(Panel(stats, title="[dim cyan]Summary[/]", border_style="dim"))


def show_table(issues: list[DiscoveredIssue], page: int = 1, page_size: int = 15) -> int:
    """Display paginated issues table. Returns total pages."""
    total_pages = max(1, (len(issues) + page_size - 1) // page_size)
    start = (page - 1) * page_size
    end = start + page_size
    page_issues = issues[start:end]

    table = Table(
        title=f"[bold cyan]Discovered GFIs[/] [dim](page {page}/{total_pages}, {len(issues)} total)[/]",
        box=box.ROUNDED,
        border_style="blue",
        header_style="bold blue",
        show_lines=False,
        padding=(0, 1),
    )

    table.add_column("#", style="dim", width=4, justify="right")
    table.add_column("Score", width=12)
    table.add_column("Title", min_width=30, max_width=50)
    table.add_column("Repository", style="cyan", max_width=30)
    table.add_column("💬", justify="right", width=4)
    table.add_column("✍️", width=3)
    table.add_column("Why", max_width=40, style="dim")

    for idx, issue in enumerate(page_issues, start=start + 1):
        has_desc = "✓" if issue.improved_description else ""
        table.add_row(
            str(idx),
            score_stars(issue.gfi_score),
            truncate(issue.title, 48),
            issue.repo.replace("canonical/", ""),
            str(issue.comments),
            has_desc,
            truncate(issue.reason, 38),
        )

    console.print(table)
    return total_pages


def show_detail(issue: DiscoveredIssue, index: int) -> None:
    """Show detailed view of a single discovered issue."""
    console.print()
    console.print(Rule(f"[bold cyan]Issue #{index}[/]"))
    console.print()

    # Header info
    info_table = Table.grid(padding=(0, 2))
    info_table.add_row(Text("Title:", style="bold"), Text(issue.title))
    info_table.add_row(Text("Repo:", style="bold"), Text(issue.repo, style="cyan"))
    info_table.add_row(Text("URL:", style="bold"), Text(issue.url, style="underline blue"))
    info_table.add_row(Text("Score:", style="bold"), score_stars(issue.gfi_score))
    info_table.add_row(Text("Labels:", style="bold"), Text(issue.labels or "none", style="dim"))
    info_table.add_row(Text("Comments:", style="bold"), Text(str(issue.comments)))
    info_table.add_row(Text("Created:", style="bold"), Text(issue.created_at))
    console.print(info_table)

    # Reason
    console.print()
    console.print(Panel(
        Text(issue.reason, style="italic"),
        title="[bold yellow]Why it's a GFI[/]",
        border_style="yellow",
    ))

    # Improved description
    if issue.improved_description:
        console.print()
        console.print(Panel(
            Markdown(issue.improved_description),
            title="[bold green]✍️  Improved Description (LLM-generated)[/]",
            border_style="green",
        ))

    # Original excerpt
    if issue.original_body_excerpt:
        console.print()
        console.print(Panel(
            Text(issue.original_body_excerpt, style="dim"),
            title="[dim]Original Body (excerpt)[/]",
            border_style="dim",
        ))


def filter_issues(issues: list[DiscoveredIssue], query: str) -> list[DiscoveredIssue]:
    """Filter issues by keyword (matches title, repo, labels, reason)."""
    query_lower = query.lower()
    return [
        i for i in issues
        if query_lower in i.title.lower()
        or query_lower in i.repo.lower()
        or query_lower in i.labels.lower()
        or query_lower in i.reason.lower()
    ]


# ---------------------------------------------------------------------------
# Main loop
# ---------------------------------------------------------------------------


def show_menu() -> None:
    """Display navigation help."""
    console.print()
    menu = Text()
    menu.append("  [n]", style="bold cyan")
    menu.append("ext  ")
    menu.append("[p]", style="bold cyan")
    menu.append("rev  ")
    menu.append("[#]", style="bold cyan")
    menu.append(" view detail  ")
    menu.append("[f]", style="bold cyan")
    menu.append("ilter  ")
    menu.append("[s]", style="bold cyan")
    menu.append("core filter  ")
    menu.append("[r]", style="bold cyan")
    menu.append("eset  ")
    menu.append("[q]", style="bold cyan")
    menu.append("uit")
    console.print(menu)


def main() -> int:
    parser = argparse.ArgumentParser(description="Browse discovered potential GFIs.")
    parser.add_argument("--csv", default=DEFAULT_CSV, help=f"CSV path (default: {DEFAULT_CSV})")
    args = parser.parse_args()

    csv_path = Path(args.csv)
    if not csv_path.exists():
        console.print(f"[bold red]❌ CSV not found: {csv_path}[/]")
        console.print("   Run gfi-discover first.")
        return 1

    all_issues = load_discovered(csv_path)
    if not all_issues:
        console.print("[bold red]❌ No issues in CSV.[/]")
        return 1

    # Sort by score descending
    all_issues.sort(key=lambda i: i.gfi_score, reverse=True)
    current_issues = all_issues
    page = 1
    page_size = 15
    active_filter = ""

    while True:
        console.clear()
        repos = len(set(i.repo for i in current_issues))
        show_header(len(current_issues), repos)
        show_stats(current_issues)

        if active_filter:
            console.print(f"  [bold yellow]Filter: '{active_filter}'[/]")

        total_pages = show_table(current_issues, page, page_size)
        show_menu()

        try:
            choice = Prompt.ask("\n[bold]Action[/]", default="n").strip().lower()
        except (EOFError, KeyboardInterrupt):
            break

        if choice == "q":
            break
        elif choice == "n":
            page = min(page + 1, total_pages)
        elif choice == "p":
            page = max(page - 1, 1)
        elif choice == "f":
            query = Prompt.ask("[bold]Search[/]", default="").strip()
            if query:
                current_issues = filter_issues(all_issues, query)
                active_filter = query
                page = 1
            else:
                current_issues = all_issues
                active_filter = ""
                page = 1
        elif choice == "s":
            try:
                min_score = IntPrompt.ask("[bold]Min score (3-5)[/]", default=4)
                current_issues = [i for i in all_issues if i.gfi_score >= min_score]
                active_filter = f"score ≥ {min_score}"
                page = 1
            except (ValueError, KeyboardInterrupt):
                pass
        elif choice == "r":
            current_issues = all_issues
            active_filter = ""
            page = 1
        elif choice.isdigit():
            idx = int(choice)
            if 1 <= idx <= len(current_issues):
                console.clear()
                show_detail(current_issues[idx - 1], idx)
                console.print()
                Prompt.ask("[dim]Press Enter to go back[/]", default="")
        else:
            pass

    console.print("\n[dim]Bye! 👋[/]")
    return 0


if __name__ == "__main__":
    sys.exit(main())
