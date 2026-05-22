# System Overview

## What It Does

GFI Scraper finds the best "good first issue" for you to contribute to. It scrapes issues from a GitHub org, scores them on how approachable they are, and optionally matches them to your developer profile using an LLM.

## Components

```
┌──────────┐       ┌─────────┐       ┌─────────────────────┐
│ GitHub   │──────▶│ Scraper │──────▶│ good_first_issues.csv│
│ API      │       │         │       └──────────┬───────────┘
└──────────┘       └─────────┘                  │
                                       ┌────────┴────────┐
                                       ▼                 ▼
                                   ┌───────┐       ┌─────────┐
                                   │  TUI  │       │ Matcher │
                                   │(Rich) │       │ (LLM)   │
                                   └───────┘       └─────────┘
```

### Scraper (`src/scrape_good_first_issues.py`)

Fetches all open "good first issue" issues from a GitHub org using the `gh` CLI. Enriches each issue with repo star counts and PR competition data (via GitHub GraphQL API). Scores, ranks, and exports to CSV.

Supports `--watch` mode for periodic auto-refresh and caches results between runs to highlight what's new.

### TUI (`src/tui.py`)

A terminal interface built with [Rich](https://github.com/Textualize/rich). Browse issues in a paginated table, filter by repo/label/keyword, view detailed score breakdowns, and trigger LLM matching — all without leaving the terminal.

### Matcher (`src/match_issues.py`)

Collects a developer profile (languages, domains, experience) and sends it along with the top issues to GPT-5.5 in a single API call. Returns a ranked list of recommendations with one-sentence explanations for each.

Cost: ~$0.003 per run.

## Scoring

Each issue gets a composite score (0–100) from six weighted signals:

| Signal       | Weight | What it measures                            |
|--------------|--------|---------------------------------------------|
| Freshness    | 25%    | How recently the issue was created          |
| Competition  | 25%    | Number of comments (fewer = less contested) |
| Availability | 20%    | Whether anyone is assigned                  |
| Popularity   | 15%    | Repository star count (log-scaled)          |
| Activity     | 10%    | How recently the issue was updated          |
| PR Status    | 5%     | Whether an open PR is targeting the issue   |

Higher score = more approachable issue.

## Data Flow

1. `gh search issues` fetches open issues labeled "good first issue"
2. `gh api graphql` batches queries for repo stars and closing PR references
3. Issues with a merged closing PR are excluded (already resolved)
4. Remaining issues are scored, diffed against the previous run, and exported to CSV
5. TUI or Matcher reads the CSV for browsing or LLM-powered matching
