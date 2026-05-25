#!/usr/bin/env python3
"""Simple web UI for browsing discovered good first issues.

A lightweight Flask app that serves the discovered GFIs CSV as an
interactive web interface with filtering, sorting, and detail views.

Usage:
    gfi-web [--csv FILE] [--port PORT]
    # Then open http://localhost:8080
"""

from __future__ import annotations

import argparse
import csv
import sys
from pathlib import Path

try:
    from flask import Flask, render_template_string, jsonify, request
except ImportError:
    print("❌ Flask not installed. Run: pip install flask")
    sys.exit(1)

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

DEFAULT_CSV = "output/discovered_gfis.csv"
DEFAULT_PORT = 8080

# ---------------------------------------------------------------------------
# App
# ---------------------------------------------------------------------------

app = Flask(__name__)
ISSUES: list[dict] = []


def load_csv(csv_path: str) -> list[dict]:
    """Load discovered GFIs from CSV."""
    issues = []
    path = Path(csv_path)
    if not path.exists():
        return []
    with path.open("r", encoding="utf-8") as fh:
        reader = csv.DictReader(fh)
        for row in reader:
            row["gfi_score"] = int(row.get("gfi_score", 3))
            row["comments"] = int(row.get("comments", 0))
            issues.append(row)
    issues.sort(key=lambda x: x["gfi_score"], reverse=True)
    return issues


# ---------------------------------------------------------------------------
# HTML Template
# ---------------------------------------------------------------------------

HTML_TEMPLATE = """
<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>🔍 Discovered Good First Issues</title>
    <style>
        * { margin: 0; padding: 0; box-sizing: border-box; }
        body {
            font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
            background: #0d1117;
            color: #c9d1d9;
            line-height: 1.6;
        }
        .container { max-width: 1400px; margin: 0 auto; padding: 20px; }

        /* Header */
        .header {
            text-align: center;
            padding: 30px 0;
            border-bottom: 1px solid #21262d;
            margin-bottom: 24px;
        }
        .header h1 {
            font-size: 2em;
            color: #58a6ff;
            margin-bottom: 8px;
        }
        .header p { color: #8b949e; font-size: 1.1em; }

        /* Stats */
        .stats {
            display: flex;
            gap: 16px;
            justify-content: center;
            flex-wrap: wrap;
            margin-bottom: 24px;
        }
        .stat-card {
            background: #161b22;
            border: 1px solid #21262d;
            border-radius: 8px;
            padding: 16px 24px;
            text-align: center;
            min-width: 120px;
        }
        .stat-card .number { font-size: 1.8em; font-weight: bold; color: #58a6ff; }
        .stat-card .label { font-size: 0.85em; color: #8b949e; margin-top: 4px; }

        /* Filters */
        .filters {
            display: flex;
            gap: 12px;
            margin-bottom: 20px;
            flex-wrap: wrap;
            align-items: center;
        }
        .filters input, .filters select {
            background: #161b22;
            border: 1px solid #30363d;
            color: #c9d1d9;
            padding: 8px 14px;
            border-radius: 6px;
            font-size: 0.95em;
        }
        .filters input:focus, .filters select:focus {
            outline: none;
            border-color: #58a6ff;
        }
        .filters input { flex: 1; min-width: 200px; }
        .filter-label { color: #8b949e; font-size: 0.9em; }

        /* Score badges */
        .score-badge {
            display: inline-flex;
            align-items: center;
            gap: 4px;
            padding: 4px 10px;
            border-radius: 12px;
            font-weight: 600;
            font-size: 0.85em;
        }
        .score-5 { background: #1b4332; color: #40c057; }
        .score-4 { background: #1a3a1a; color: #69db7c; }
        .score-3 { background: #3d2e00; color: #ffd43b; }

        /* Issue cards */
        .issues-grid { display: flex; flex-direction: column; gap: 12px; }
        .issue-card {
            background: #161b22;
            border: 1px solid #21262d;
            border-radius: 8px;
            padding: 16px 20px;
            transition: border-color 0.2s, transform 0.1s;
            cursor: pointer;
        }
        .issue-card:hover {
            border-color: #58a6ff;
            transform: translateX(4px);
        }
        .issue-card.expanded { border-color: #58a6ff; }

        .issue-header {
            display: flex;
            align-items: flex-start;
            gap: 12px;
            margin-bottom: 8px;
        }
        .issue-title {
            font-size: 1.05em;
            font-weight: 600;
            color: #c9d1d9;
            flex: 1;
        }
        .issue-title a {
            color: #58a6ff;
            text-decoration: none;
        }
        .issue-title a:hover { text-decoration: underline; }

        .issue-meta {
            display: flex;
            gap: 16px;
            color: #8b949e;
            font-size: 0.85em;
            flex-wrap: wrap;
        }
        .issue-meta span { display: inline-flex; align-items: center; gap: 4px; }

        .issue-reason {
            margin-top: 8px;
            padding: 8px 12px;
            background: #0d1117;
            border-radius: 6px;
            font-size: 0.9em;
            color: #8b949e;
            font-style: italic;
        }

        /* Expanded detail */
        .issue-detail {
            display: none;
            margin-top: 16px;
            padding-top: 16px;
            border-top: 1px solid #21262d;
        }
        .issue-card.expanded .issue-detail { display: block; }

        .improved-desc {
            background: #0d1117;
            border: 1px solid #1b4332;
            border-radius: 8px;
            padding: 16px;
            margin-top: 12px;
        }
        .improved-desc h4 {
            color: #40c057;
            margin-bottom: 10px;
            font-size: 0.95em;
        }
        .improved-desc .content {
            font-size: 0.9em;
            white-space: pre-wrap;
            line-height: 1.7;
        }
        .improved-desc .content h2,
        .improved-desc .content h3 {
            color: #58a6ff;
            margin-top: 12px;
            margin-bottom: 6px;
        }

        .original-excerpt {
            background: #0d1117;
            border: 1px solid #30363d;
            border-radius: 8px;
            padding: 12px;
            margin-top: 12px;
            font-size: 0.85em;
            color: #8b949e;
        }
        .original-excerpt h4 { color: #8b949e; margin-bottom: 8px; }

        .labels {
            display: flex;
            gap: 6px;
            flex-wrap: wrap;
            margin-top: 8px;
        }
        .label-tag {
            background: #1f2937;
            border: 1px solid #374151;
            padding: 2px 8px;
            border-radius: 12px;
            font-size: 0.75em;
            color: #9ca3af;
        }

        /* Pagination */
        .pagination {
            display: flex;
            justify-content: center;
            gap: 8px;
            margin-top: 24px;
            align-items: center;
        }
        .pagination button {
            background: #21262d;
            border: 1px solid #30363d;
            color: #c9d1d9;
            padding: 8px 16px;
            border-radius: 6px;
            cursor: pointer;
            font-size: 0.9em;
        }
        .pagination button:hover { background: #30363d; }
        .pagination button:disabled { opacity: 0.4; cursor: not-allowed; }
        .pagination .page-info { color: #8b949e; }

        /* Empty state */
        .empty-state {
            text-align: center;
            padding: 60px 20px;
            color: #8b949e;
        }
        .empty-state h3 { color: #c9d1d9; margin-bottom: 8px; }

        /* Footer */
        .footer {
            text-align: center;
            padding: 24px 0;
            margin-top: 40px;
            border-top: 1px solid #21262d;
            color: #8b949e;
            font-size: 0.85em;
        }
    </style>
</head>
<body>
    <div class="container">
        <div class="header">
            <h1>🔍 Discovered Good First Issues</h1>
            <p>Potential GFIs identified by LLM from observability repos</p>
        </div>

        <div class="stats" id="stats"></div>

        <div class="filters">
            <span class="filter-label">🔎</span>
            <input type="text" id="search" placeholder="Search by title, repo, or reason..." oninput="filterIssues()">
            <select id="scoreFilter" onchange="filterIssues()">
                <option value="0">All scores</option>
                <option value="5">⭐⭐⭐⭐⭐ only</option>
                <option value="4">⭐⭐⭐⭐ and above</option>
                <option value="3">⭐⭐⭐ and above</option>
            </select>
            <select id="repoFilter" onchange="filterIssues()">
                <option value="">All repos</option>
            </select>
        </div>

        <div class="issues-grid" id="issuesGrid"></div>

        <div class="pagination" id="pagination"></div>

        <div class="footer">
            Built with gfi-scraper • LLM-powered issue discovery for Canonical Observability
        </div>
    </div>

    <script>
        const allIssues = {{ issues_json | safe }};
        let filteredIssues = [...allIssues];
        let currentPage = 1;
        const pageSize = 20;

        // Initialize
        document.addEventListener('DOMContentLoaded', () => {
            renderStats();
            populateRepoFilter();
            filterIssues();
        });

        function renderStats() {
            const stats = document.getElementById('stats');
            const score5 = allIssues.filter(i => i.gfi_score === 5).length;
            const score4 = allIssues.filter(i => i.gfi_score === 4).length;
            const score3 = allIssues.filter(i => i.gfi_score === 3).length;
            const repos = new Set(allIssues.map(i => i.repo)).size;
            const withDesc = allIssues.filter(i => i.improved_description).length;

            stats.innerHTML = `
                <div class="stat-card"><div class="number">${allIssues.length}</div><div class="label">Total GFIs</div></div>
                <div class="stat-card"><div class="number" style="color:#40c057">${score5}</div><div class="label">⭐⭐⭐⭐⭐</div></div>
                <div class="stat-card"><div class="number" style="color:#69db7c">${score4}</div><div class="label">⭐⭐⭐⭐</div></div>
                <div class="stat-card"><div class="number" style="color:#ffd43b">${score3}</div><div class="label">⭐⭐⭐</div></div>
                <div class="stat-card"><div class="number">${repos}</div><div class="label">Repos</div></div>
                <div class="stat-card"><div class="number">${withDesc}</div><div class="label">With improved desc</div></div>
            `;
        }

        function populateRepoFilter() {
            const repos = [...new Set(allIssues.map(i => i.repo))].sort();
            const select = document.getElementById('repoFilter');
            repos.forEach(repo => {
                const opt = document.createElement('option');
                opt.value = repo;
                opt.textContent = repo.replace('canonical/', '');
                select.appendChild(opt);
            });
        }

        function filterIssues() {
            const search = document.getElementById('search').value.toLowerCase();
            const minScore = parseInt(document.getElementById('scoreFilter').value);
            const repo = document.getElementById('repoFilter').value;

            filteredIssues = allIssues.filter(issue => {
                if (minScore && issue.gfi_score < minScore) return false;
                if (repo && issue.repo !== repo) return false;
                if (search) {
                    const text = (issue.title + ' ' + issue.repo + ' ' + issue.reason + ' ' + issue.labels).toLowerCase();
                    if (!text.includes(search)) return false;
                }
                return true;
            });

            currentPage = 1;
            renderIssues();
        }

        function renderIssues() {
            const grid = document.getElementById('issuesGrid');
            const totalPages = Math.max(1, Math.ceil(filteredIssues.length / pageSize));
            const start = (currentPage - 1) * pageSize;
            const pageIssues = filteredIssues.slice(start, start + pageSize);

            if (pageIssues.length === 0) {
                grid.innerHTML = '<div class="empty-state"><h3>No issues match your filters</h3><p>Try adjusting your search or filters</p></div>';
                document.getElementById('pagination').innerHTML = '';
                return;
            }

            grid.innerHTML = pageIssues.map((issue, idx) => {
                const globalIdx = start + idx;
                const scoreClass = `score-${issue.gfi_score}`;
                const stars = '⭐'.repeat(issue.gfi_score);
                const labels = issue.labels ? issue.labels.split('; ').filter(l => l).map(l => `<span class="label-tag">${l}</span>`).join('') : '';
                const repoShort = issue.repo.replace('canonical/', '');

                let detailHtml = '';
                if (issue.improved_description) {
                    const desc = issue.improved_description.replace(/</g, '&lt;').replace(/>/g, '&gt;');
                    detailHtml += `<div class="improved-desc"><h4>✍️ Improved Description (LLM-generated)</h4><div class="content">${formatMarkdown(issue.improved_description)}</div></div>`;
                }
                if (issue.original_body_excerpt) {
                    detailHtml += `<div class="original-excerpt"><h4>📄 Original Body (excerpt)</h4>${issue.original_body_excerpt}</div>`;
                }

                return `
                    <div class="issue-card" id="card-${globalIdx}" onclick="toggleCard(${globalIdx})">
                        <div class="issue-header">
                            <span class="score-badge ${scoreClass}">${stars}</span>
                            <div class="issue-title">
                                <a href="${issue.url}" target="_blank" onclick="event.stopPropagation()">${issue.title}</a>
                            </div>
                        </div>
                        <div class="issue-meta">
                            <span>📦 ${repoShort}</span>
                            <span>💬 ${issue.comments}</span>
                            <span>📅 ${issue.created_at}</span>
                            ${issue.improved_description ? '<span>✍️ has improved desc</span>' : ''}
                        </div>
                        ${labels ? `<div class="labels">${labels}</div>` : ''}
                        <div class="issue-reason">💡 ${issue.reason}</div>
                        <div class="issue-detail">${detailHtml}</div>
                    </div>
                `;
            }).join('');

            // Pagination
            document.getElementById('pagination').innerHTML = `
                <button onclick="goPage(${currentPage - 1})" ${currentPage === 1 ? 'disabled' : ''}>← Prev</button>
                <span class="page-info">Page ${currentPage} of ${totalPages} (${filteredIssues.length} issues)</span>
                <button onclick="goPage(${currentPage + 1})" ${currentPage === totalPages ? 'disabled' : ''}>Next →</button>
            `;
        }

        function toggleCard(idx) {
            const card = document.getElementById(`card-${idx}`);
            card.classList.toggle('expanded');
        }

        function goPage(page) {
            const totalPages = Math.ceil(filteredIssues.length / pageSize);
            currentPage = Math.max(1, Math.min(page, totalPages));
            renderIssues();
            window.scrollTo({ top: 0, behavior: 'smooth' });
        }

        function formatMarkdown(text) {
            // Basic markdown formatting
            return text
                .replace(/&/g, '&amp;')
                .replace(/</g, '&lt;')
                .replace(/>/g, '&gt;')
                .replace(/^## (.+)$/gm, '<h3>$1</h3>')
                .replace(/^### (.+)$/gm, '<h4>$1</h4>')
                .replace(/\\*\\*(.+?)\\*\\*/g, '<strong>$1</strong>')
                .replace(/`([^`]+)`/g, '<code style="background:#21262d;padding:2px 6px;border-radius:3px">$1</code>')
                .replace(/^- (.+)$/gm, '• $1')
                .replace(/\\n/g, '<br>');
        }
    </script>
</body>
</html>
"""


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------


@app.route("/")
def index():
    import json
    issues_json = json.dumps(ISSUES)
    return render_template_string(HTML_TEMPLATE, issues_json=issues_json)


@app.route("/api/issues")
def api_issues():
    return jsonify(ISSUES)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Web UI for browsing discovered GFIs.")
    parser.add_argument("--csv", default=DEFAULT_CSV, help=f"CSV path (default: {DEFAULT_CSV})")
    parser.add_argument("--port", "-p", type=int, default=DEFAULT_PORT, help=f"Port (default: {DEFAULT_PORT})")
    parser.add_argument("--host", default="0.0.0.0", help="Host (default: 0.0.0.0)")
    return parser.parse_args()


def main() -> int:
    args = parse_args()

    global ISSUES
    ISSUES = load_csv(args.csv)

    if not ISSUES:
        print(f"❌ No issues found in {args.csv}")
        print("   Run gfi-discover first.")
        return 1

    print(f"📋 Loaded {len(ISSUES)} discovered GFIs from {args.csv}")
    print(f"🌐 Starting web UI at http://localhost:{args.port}")
    print(f"   Press Ctrl+C to stop.\n")

    app.run(host=args.host, port=args.port, debug=False)
    return 0


if __name__ == "__main__":
    sys.exit(main())
