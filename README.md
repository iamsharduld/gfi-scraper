# 🎯 Good First Issue Finder

Discover, rank, and get personalized recommendations for "good first issue" contributions in Canonical's GitHub repositories.

## Features

- **Scrape** all open "good first issues" across 76+ Canonical repos (206 issues)
- **Rank** using a weighted heuristic (freshness, competition, availability, popularity, activity, linked PRs)
- **Match** issues to your developer profile using GPT-5.5 (single API call, ~$0.002)
- **Cache & diff** between runs — see what's new since last time
- **Auto-refresh** with `--watch` mode or cron scheduling
- **Beautiful TUI** — browse, filter, and match interactively in the terminal

## Quick Start

### Prerequisites

- Python 3.12+
- [`gh` CLI](https://cli.github.com/) authenticated (`gh auth login`)
- [OpenAI API key](https://platform.openai.com/api-keys) (for LLM matching only)

### Install

```bash
pip install .

# Or for development (editable install with test dependencies)
pip install -e ".[dev]"
```

### Run

```bash
# 1. Scrape and rank all issues
gfi-scrape

# 2. Interactive TUI (browse, filter, match)
export OPENAI_API_KEY='sk-...'
gfi-tui

# 3. Or use the headless matcher
gfi-match
```

## Project Structure

```
├── pyproject.toml
├── README.md
├── docs/
│   ├── architecture.md        # System design & data flow
│   └── scoring.md             # Ranking heuristic explained
├── src/gfi_scraper/
│   ├── __init__.py
│   ├── scrape_good_first_issues.py   # Scraper + ranker + cache
│   ├── match_issues.py               # LLM-powered matcher
│   └── tui.py                        # Interactive terminal UI
├── tests/
│   └── test_all.py            # 96 unit tests
├── .cache/                    # Run-to-run diff cache (gitignored)
└── good_first_issues.csv      # Latest scraped results
```

## Usage

### Scraper

```bash
# Basic run
gfi-scrape

# Custom org
gfi-scrape --org ubuntu

# Auto-refresh every 4 hours
gfi-scrape --watch --interval 4

# Generate crontab entry
gfi-scrape --cron
```

### TUI

```bash
gfi-tui
```

| Key | Action |
|-----|--------|
| `b` | Browse all issues (paginated) |
| `n` | What's new (since last run) |
| `f` | Filter by keyword |
| `d` | Detail view of a specific issue |
| `m` | Match to your profile (LLM) |
| `s` | Stats overview |
| `q` | Quit |

### Matcher (headless)

```bash
gfi-match --top 15
```

## How Scoring Works

Each issue is scored 0–100 using a weighted composite:

| Signal | Weight | Logic |
|--------|--------|-------|
| Freshness | 25% | Exponential decay (half-life: 180 days) |
| Competition | 25% | Fewer comments = higher score (cap: 10) |
| Availability | 20% | No assignees = 100, decays per assignee |
| Popularity | 15% | Repo stars, log-scaled |
| Activity | 10% | Staleness gate (updated within 1 year?) |
| PR Status | 5% | Open PR = competition penalty |

## Testing

```bash
python3 -m pytest tests/ -v
```

96 tests covering: scoring functions, body extraction, CSV round-trips, caching/diffing, GraphQL parsing, LLM prompt building, TUI helpers, integration, and edge cases.

## Cost

- **Scraping**: Free (uses `gh` CLI with your GitHub token)
- **LLM matching**: ~$0.002 per run (single GPT-5.5 call, ~10k tokens)

## License

MIT
