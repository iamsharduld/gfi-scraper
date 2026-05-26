#!/usr/bin/env python3
"""Web UI for browsing discovered good first issues.

A polished Flask app that serves the discovered GFIs CSV as a modern
dashboard with filtering, sorting, and detail views.

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
    <title>GFI Discovery — Canonical Observability</title>
    <link rel="preconnect" href="https://fonts.googleapis.com">
    <link href="https://fonts.googleapis.com/css2?family=Inter:wght@300;400;500;600;700&display=swap" rel="stylesheet">
    <style>
        :root {
            --bg-primary: #0a0e14;
            --bg-secondary: #11151c;
            --bg-card: #151b24;
            --bg-hover: #1a2230;
            --bg-input: #0d1219;
            --border: #1e2a3a;
            --border-active: #3b82f6;
            --text-primary: #e2e8f0;
            --text-secondary: #94a3b8;
            --text-muted: #64748b;
            --accent: #3b82f6;
            --accent-hover: #60a5fa;
            --green: #10b981;
            --green-dim: #064e3b;
            --yellow: #f59e0b;
            --yellow-dim: #451a03;
            --purple: #8b5cf6;
            --purple-dim: #2e1065;
            --red: #ef4444;
            --radius: 12px;
            --radius-sm: 8px;
            --shadow: 0 4px 6px -1px rgba(0,0,0,0.3), 0 2px 4px -2px rgba(0,0,0,0.2);
            --shadow-lg: 0 10px 15px -3px rgba(0,0,0,0.4), 0 4px 6px -4px rgba(0,0,0,0.3);
        }

        * { margin: 0; padding: 0; box-sizing: border-box; }
        body {
            font-family: 'Inter', -apple-system, BlinkMacSystemFont, sans-serif;
            background: var(--bg-primary);
            color: var(--text-primary);
            line-height: 1.6;
            min-height: 100vh;
        }

        /* Layout */
        .app { display: flex; flex-direction: column; min-height: 100vh; }

        /* Top nav */
        .topnav {
            background: var(--bg-secondary);
            border-bottom: 1px solid var(--border);
            padding: 14px 32px;
            display: flex;
            align-items: center;
            justify-content: space-between;
            position: sticky;
            top: 0;
            z-index: 100;
            backdrop-filter: blur(12px);
        }
        .topnav-brand {
            display: flex;
            align-items: center;
            gap: 12px;
        }
        .topnav-brand .logo {
            width: 32px;
            height: 32px;
            background: linear-gradient(135deg, var(--accent), var(--purple));
            border-radius: 8px;
            display: flex;
            align-items: center;
            justify-content: center;
            font-size: 16px;
        }
        .topnav-brand h1 {
            font-size: 1.1em;
            font-weight: 600;
            color: var(--text-primary);
        }
        .topnav-brand span {
            font-size: 0.8em;
            color: var(--text-muted);
            font-weight: 400;
        }
        .topnav-actions {
            display: flex;
            gap: 12px;
            align-items: center;
        }
        .btn-subtle {
            background: transparent;
            border: 1px solid var(--border);
            color: var(--text-secondary);
            padding: 6px 14px;
            border-radius: var(--radius-sm);
            font-size: 0.82em;
            cursor: pointer;
            transition: all 0.2s;
            text-decoration: none;
        }
        .btn-subtle:hover {
            background: var(--bg-hover);
            border-color: var(--border-active);
            color: var(--text-primary);
        }

        /* Main content */
        .main { flex: 1; padding: 32px; max-width: 1440px; margin: 0 auto; width: 100%; }

        /* Stats row */
        .stats-row {
            display: grid;
            grid-template-columns: repeat(auto-fit, minmax(160px, 1fr));
            gap: 16px;
            margin-bottom: 32px;
        }
        .stat-card {
            background: var(--bg-card);
            border: 1px solid var(--border);
            border-radius: var(--radius);
            padding: 20px;
            position: relative;
            overflow: hidden;
            transition: transform 0.2s, box-shadow 0.2s;
        }
        .stat-card:hover {
            transform: translateY(-2px);
            box-shadow: var(--shadow-lg);
        }
        .stat-card::before {
            content: '';
            position: absolute;
            top: 0; left: 0; right: 0;
            height: 3px;
            background: var(--accent);
            opacity: 0.6;
        }
        .stat-card.green::before { background: var(--green); }
        .stat-card.yellow::before { background: var(--yellow); }
        .stat-card.purple::before { background: var(--purple); }
        .stat-value { font-size: 2em; font-weight: 700; color: var(--text-primary); }
        .stat-label { font-size: 0.82em; color: var(--text-muted); margin-top: 4px; font-weight: 500; text-transform: uppercase; letter-spacing: 0.5px; }

        /* Toolbar */
        .toolbar {
            display: flex;
            gap: 12px;
            margin-bottom: 24px;
            flex-wrap: wrap;
            align-items: center;
        }
        .search-box {
            flex: 1;
            min-width: 280px;
            position: relative;
        }
        .search-box input {
            width: 100%;
            background: var(--bg-input);
            border: 1px solid var(--border);
            color: var(--text-primary);
            padding: 10px 14px 10px 40px;
            border-radius: var(--radius-sm);
            font-size: 0.9em;
            font-family: inherit;
            transition: border-color 0.2s, box-shadow 0.2s;
        }
        .search-box input:focus {
            outline: none;
            border-color: var(--accent);
            box-shadow: 0 0 0 3px rgba(59,130,246,0.1);
        }
        .search-box input::placeholder { color: var(--text-muted); }
        .search-box .icon {
            position: absolute;
            left: 12px;
            top: 50%;
            transform: translateY(-50%);
            color: var(--text-muted);
            font-size: 14px;
        }
        .filter-select {
            background: var(--bg-input);
            border: 1px solid var(--border);
            color: var(--text-secondary);
            padding: 10px 14px;
            border-radius: var(--radius-sm);
            font-size: 0.9em;
            font-family: inherit;
            cursor: pointer;
            transition: border-color 0.2s;
            min-width: 140px;
        }
        .filter-select:focus {
            outline: none;
            border-color: var(--accent);
        }
        .sort-btn {
            background: var(--bg-input);
            border: 1px solid var(--border);
            color: var(--text-secondary);
            padding: 10px 14px;
            border-radius: var(--radius-sm);
            font-size: 0.9em;
            cursor: pointer;
            transition: all 0.2s;
            white-space: nowrap;
        }
        .sort-btn:hover, .sort-btn.active {
            border-color: var(--accent);
            color: var(--accent);
        }
        .results-count {
            color: var(--text-muted);
            font-size: 0.85em;
            padding: 0 8px;
            white-space: nowrap;
        }

        /* View toggle */
        .view-toggle {
            display: flex;
            border: 1px solid var(--border);
            border-radius: var(--radius-sm);
            overflow: hidden;
        }
        .view-toggle button {
            background: var(--bg-input);
            border: none;
            color: var(--text-muted);
            padding: 8px 12px;
            cursor: pointer;
            font-size: 14px;
            transition: all 0.2s;
        }
        .view-toggle button.active {
            background: var(--accent);
            color: white;
        }
        .view-toggle button:not(:last-child) { border-right: 1px solid var(--border); }

        /* Issue list */
        .issues-list { display: flex; flex-direction: column; gap: 8px; }

        .issue-card {
            background: var(--bg-card);
            border: 1px solid var(--border);
            border-radius: var(--radius);
            padding: 20px 24px;
            cursor: pointer;
            transition: all 0.2s ease;
            position: relative;
        }
        .issue-card:hover {
            background: var(--bg-hover);
            border-color: rgba(59,130,246,0.3);
            box-shadow: var(--shadow);
        }
        .issue-card.expanded {
            border-color: var(--accent);
            box-shadow: 0 0 0 1px var(--accent), var(--shadow-lg);
        }

        .issue-top {
            display: flex;
            align-items: flex-start;
            gap: 16px;
        }

        /* Score indicator */
        .score-pill {
            display: flex;
            align-items: center;
            gap: 6px;
            padding: 4px 10px;
            border-radius: 20px;
            font-size: 0.78em;
            font-weight: 600;
            white-space: nowrap;
            flex-shrink: 0;
        }
        .score-pill.s5 { background: var(--green-dim); color: var(--green); border: 1px solid rgba(16,185,129,0.2); }
        .score-pill.s4 { background: rgba(59,130,246,0.1); color: var(--accent-hover); border: 1px solid rgba(59,130,246,0.2); }
        .score-pill.s3 { background: var(--yellow-dim); color: var(--yellow); border: 1px solid rgba(245,158,11,0.2); }

        .score-dots {
            display: flex;
            gap: 3px;
        }
        .score-dot {
            width: 6px;
            height: 6px;
            border-radius: 50%;
            background: currentColor;
            opacity: 0.3;
        }
        .score-dot.filled { opacity: 1; }

        .issue-content { flex: 1; min-width: 0; }
        .issue-title-row {
            display: flex;
            align-items: baseline;
            gap: 10px;
            margin-bottom: 6px;
        }
        .issue-title {
            font-size: 1em;
            font-weight: 600;
            color: var(--text-primary);
            line-height: 1.4;
        }
        .issue-title a {
            color: inherit;
            text-decoration: none;
            transition: color 0.2s;
        }
        .issue-title a:hover { color: var(--accent-hover); }

        .issue-meta-row {
            display: flex;
            gap: 16px;
            color: var(--text-muted);
            font-size: 0.8em;
            margin-bottom: 10px;
            flex-wrap: wrap;
            align-items: center;
        }
        .meta-item {
            display: inline-flex;
            align-items: center;
            gap: 5px;
        }
        .meta-item .dot {
            width: 6px; height: 6px;
            border-radius: 50%;
            background: var(--text-muted);
            opacity: 0.5;
        }

        .issue-reason-text {
            font-size: 0.88em;
            color: var(--text-secondary);
            line-height: 1.5;
            padding: 10px 14px;
            background: rgba(59,130,246,0.04);
            border-left: 3px solid rgba(59,130,246,0.3);
            border-radius: 0 var(--radius-sm) var(--radius-sm) 0;
            margin-top: 10px;
        }

        .labels-row {
            display: flex;
            gap: 6px;
            flex-wrap: wrap;
            margin-top: 10px;
        }
        .label-chip {
            background: rgba(139,92,246,0.1);
            border: 1px solid rgba(139,92,246,0.2);
            padding: 2px 10px;
            border-radius: 20px;
            font-size: 0.72em;
            color: var(--purple);
            font-weight: 500;
        }

        .expand-hint {
            position: absolute;
            right: 20px;
            top: 20px;
            color: var(--text-muted);
            font-size: 12px;
            opacity: 0;
            transition: opacity 0.2s;
        }
        .issue-card:hover .expand-hint { opacity: 1; }
        .issue-card.expanded .expand-hint { opacity: 1; }

        /* Detail panel */
        .issue-detail {
            display: none;
            margin-top: 20px;
            padding-top: 20px;
            border-top: 1px solid var(--border);
            animation: slideDown 0.2s ease-out;
        }
        .issue-card.expanded .issue-detail { display: block; }

        @keyframes slideDown {
            from { opacity: 0; transform: translateY(-8px); }
            to { opacity: 1; transform: translateY(0); }
        }

        .detail-section {
            margin-bottom: 20px;
        }
        .detail-section:last-child { margin-bottom: 0; }
        .detail-label {
            font-size: 0.75em;
            font-weight: 600;
            text-transform: uppercase;
            letter-spacing: 0.8px;
            color: var(--text-muted);
            margin-bottom: 10px;
            display: flex;
            align-items: center;
            gap: 6px;
        }
        .detail-label .indicator {
            width: 8px; height: 8px;
            border-radius: 2px;
        }
        .detail-label .indicator.green { background: var(--green); }
        .detail-label .indicator.blue { background: var(--accent); }

        .improved-content {
            background: var(--bg-primary);
            border: 1px solid rgba(16,185,129,0.15);
            border-radius: var(--radius-sm);
            padding: 20px;
            font-size: 0.88em;
            line-height: 1.8;
            color: var(--text-secondary);
        }
        .improved-content h2, .improved-content h3 {
            color: var(--text-primary);
            font-size: 1em;
            margin: 16px 0 8px;
            font-weight: 600;
        }
        .improved-content h2:first-child, .improved-content h3:first-child { margin-top: 0; }
        .improved-content ul { padding-left: 20px; margin: 8px 0; }
        .improved-content li { margin: 4px 0; }
        .improved-content code {
            background: rgba(59,130,246,0.1);
            padding: 2px 6px;
            border-radius: 4px;
            font-size: 0.9em;
            color: var(--accent-hover);
        }
        .improved-content p { margin: 8px 0; }

        .original-content {
            background: var(--bg-primary);
            border: 1px solid var(--border);
            border-radius: var(--radius-sm);
            padding: 16px;
            font-size: 0.82em;
            color: var(--text-muted);
            font-style: italic;
            line-height: 1.6;
        }

        /* Grid view */
        .issues-grid-view {
            display: grid;
            grid-template-columns: repeat(auto-fill, minmax(380px, 1fr));
            gap: 16px;
        }
        .issues-grid-view .issue-card { height: fit-content; }
        .issues-grid-view .issue-card .expand-hint { display: none; }

        /* Pagination */
        .pagination-bar {
            display: flex;
            justify-content: center;
            align-items: center;
            gap: 8px;
            margin-top: 32px;
            padding: 16px 0;
        }
        .page-btn {
            background: var(--bg-card);
            border: 1px solid var(--border);
            color: var(--text-secondary);
            padding: 8px 14px;
            border-radius: var(--radius-sm);
            cursor: pointer;
            font-size: 0.85em;
            font-family: inherit;
            transition: all 0.2s;
        }
        .page-btn:hover:not(:disabled) {
            background: var(--bg-hover);
            border-color: var(--accent);
            color: var(--accent);
        }
        .page-btn:disabled { opacity: 0.3; cursor: not-allowed; }
        .page-btn.active { background: var(--accent); color: white; border-color: var(--accent); }
        .page-info { color: var(--text-muted); font-size: 0.82em; padding: 0 12px; }

        /* Empty state */
        .empty-state {
            text-align: center;
            padding: 80px 20px;
            color: var(--text-muted);
        }
        .empty-state .icon { font-size: 3em; margin-bottom: 16px; opacity: 0.5; }
        .empty-state h3 { color: var(--text-secondary); margin-bottom: 8px; font-weight: 500; }

        /* Responsive */
        @media (max-width: 768px) {
            .main { padding: 16px; }
            .topnav { padding: 12px 16px; }
            .stats-row { grid-template-columns: repeat(2, 1fr); }
            .toolbar { flex-direction: column; align-items: stretch; }
            .search-box { min-width: unset; }
            .issues-grid-view { grid-template-columns: 1fr; }
        }

        /* Scrollbar */
        ::-webkit-scrollbar { width: 8px; }
        ::-webkit-scrollbar-track { background: var(--bg-primary); }
        ::-webkit-scrollbar-thumb { background: var(--border); border-radius: 4px; }
        ::-webkit-scrollbar-thumb:hover { background: var(--text-muted); }
    </style>
</head>
<body>
    <div class="app">
        <nav class="topnav">
            <div class="topnav-brand">
                <div class="logo">🔬</div>
                <div>
                    <h1>GFI Discovery</h1>
                    <span>Canonical Observability</span>
                </div>
            </div>
            <div class="topnav-actions">
                <a href="/api/issues" target="_blank" class="btn-subtle">⬇ JSON API</a>
                <button class="btn-subtle" onclick="exportCSV()">📄 Export CSV</button>
            </div>
        </nav>

        <main class="main">
            <div class="stats-row" id="stats"></div>

            <div class="toolbar">
                <div class="search-box">
                    <span class="icon">🔍</span>
                    <input type="text" id="search" placeholder="Search issues by title, repo, reason, or labels..." oninput="filterIssues()">
                </div>
                <select class="filter-select" id="scoreFilter" onchange="filterIssues()">
                    <option value="0">All Scores</option>
                    <option value="5">Score 5 — Excellent</option>
                    <option value="4">Score 4+ — Good</option>
                    <option value="3">Score 3+ — Maybe</option>
                </select>
                <select class="filter-select" id="repoFilter" onchange="filterIssues()">
                    <option value="">All Repos</option>
                </select>
                <select class="filter-select" id="sortBy" onchange="filterIssues()">
                    <option value="score">Sort: Score ↓</option>
                    <option value="date">Sort: Newest</option>
                    <option value="comments">Sort: Most discussed</option>
                    <option value="repo">Sort: Repo A-Z</option>
                </select>
                <div class="view-toggle">
                    <button id="viewList" class="active" onclick="setView('list')" title="List view">☰</button>
                    <button id="viewGrid" onclick="setView('grid')" title="Grid view">⊞</button>
                </div>
                <span class="results-count" id="resultsCount"></span>
            </div>

            <div class="issues-list" id="issuesContainer"></div>

            <div class="pagination-bar" id="pagination"></div>
        </main>
    </div>

    <script>
        const allIssues = {{ issues_json | safe }};
        let filteredIssues = [...allIssues];
        let currentPage = 1;
        let currentView = 'list';
        const pageSize = 15;

        document.addEventListener('DOMContentLoaded', () => {
            renderStats();
            populateRepoFilter();
            filterIssues();
        });

        function renderStats() {
            const el = document.getElementById('stats');
            const score5 = allIssues.filter(i => i.gfi_score === 5).length;
            const score4 = allIssues.filter(i => i.gfi_score === 4).length;
            const score3 = allIssues.filter(i => i.gfi_score === 3).length;
            const repos = new Set(allIssues.map(i => i.repo)).size;
            const withDesc = allIssues.filter(i => i.improved_description).length;

            el.innerHTML = `
                <div class="stat-card">
                    <div class="stat-value">${allIssues.length}</div>
                    <div class="stat-label">Total Issues</div>
                </div>
                <div class="stat-card green">
                    <div class="stat-value">${score5}</div>
                    <div class="stat-label">Excellent (5/5)</div>
                </div>
                <div class="stat-card">
                    <div class="stat-value">${score4}</div>
                    <div class="stat-label">Good (4/5)</div>
                </div>
                <div class="stat-card yellow">
                    <div class="stat-value">${score3}</div>
                    <div class="stat-label">Maybe (3/5)</div>
                </div>
                <div class="stat-card purple">
                    <div class="stat-value">${repos}</div>
                    <div class="stat-label">Repositories</div>
                </div>
                <div class="stat-card">
                    <div class="stat-value">${withDesc}</div>
                    <div class="stat-label">Improved Descriptions</div>
                </div>
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

        function setView(view) {
            currentView = view;
            document.getElementById('viewList').classList.toggle('active', view === 'list');
            document.getElementById('viewGrid').classList.toggle('active', view === 'grid');
            renderIssues();
        }

        function filterIssues() {
            const search = document.getElementById('search').value.toLowerCase();
            const minScore = parseInt(document.getElementById('scoreFilter').value);
            const repo = document.getElementById('repoFilter').value;
            const sortBy = document.getElementById('sortBy').value;

            filteredIssues = allIssues.filter(issue => {
                if (minScore && issue.gfi_score < minScore) return false;
                if (repo && issue.repo !== repo) return false;
                if (search) {
                    const text = (issue.title + ' ' + issue.repo + ' ' + (issue.reason || '') + ' ' + (issue.labels || '')).toLowerCase();
                    if (!text.includes(search)) return false;
                }
                return true;
            });

            filteredIssues.sort((a, b) => {
                switch(sortBy) {
                    case 'score': return b.gfi_score - a.gfi_score;
                    case 'date': return (b.created_at || '').localeCompare(a.created_at || '');
                    case 'comments': return b.comments - a.comments;
                    case 'repo': return (a.repo || '').localeCompare(b.repo || '');
                    default: return 0;
                }
            });

            currentPage = 1;
            renderIssues();
        }

        function renderIssues() {
            const container = document.getElementById('issuesContainer');
            const totalPages = Math.max(1, Math.ceil(filteredIssues.length / pageSize));
            const start = (currentPage - 1) * pageSize;
            const pageIssues = filteredIssues.slice(start, start + pageSize);

            document.getElementById('resultsCount').textContent = `${filteredIssues.length} results`;

            container.className = currentView === 'grid' ? 'issues-grid-view' : 'issues-list';

            if (pageIssues.length === 0) {
                container.innerHTML = `
                    <div class="empty-state">
                        <div class="icon">🔍</div>
                        <h3>No issues match your filters</h3>
                        <p>Try adjusting your search terms or filters</p>
                    </div>`;
                document.getElementById('pagination').innerHTML = '';
                return;
            }

            container.innerHTML = pageIssues.map((issue, idx) => {
                const globalIdx = start + idx;
                const scoreClass = `s${issue.gfi_score}`;
                const repoShort = issue.repo.replace('canonical/', '');
                const labels = issue.labels ? issue.labels.split('; ').filter(l => l) : [];

                const scoreDots = Array.from({length: 5}, (_, i) =>
                    `<span class="score-dot ${i < issue.gfi_score ? 'filled' : ''}"></span>`
                ).join('');

                let detailHtml = '';
                if (issue.improved_description) {
                    detailHtml += `
                        <div class="detail-section">
                            <div class="detail-label"><span class="indicator green"></span>LLM-Improved Description</div>
                            <div class="improved-content">${formatMarkdown(issue.improved_description)}</div>
                        </div>`;
                }
                if (issue.original_body_excerpt) {
                    detailHtml += `
                        <div class="detail-section">
                            <div class="detail-label"><span class="indicator blue"></span>Original Excerpt</div>
                            <div class="original-content">${escapeHtml(issue.original_body_excerpt)}</div>
                        </div>`;
                }

                return `
                    <div class="issue-card" id="card-${globalIdx}" onclick="toggleCard(${globalIdx})">
                        <span class="expand-hint">${'▼ click to expand'}</span>
                        <div class="issue-top">
                            <span class="score-pill ${scoreClass}">
                                <span class="score-dots">${scoreDots}</span>
                                ${issue.gfi_score}/5
                            </span>
                            <div class="issue-content">
                                <div class="issue-title">
                                    <a href="${issue.url}" target="_blank" onclick="event.stopPropagation()">${escapeHtml(issue.title)}</a>
                                </div>
                                <div class="issue-meta-row">
                                    <span class="meta-item">📦 ${repoShort}</span>
                                    <span class="meta-item">💬 ${issue.comments} comments</span>
                                    <span class="meta-item">📅 ${issue.created_at}</span>
                                    ${issue.improved_description ? '<span class="meta-item">✍️ improved</span>' : ''}
                                </div>
                                <div class="issue-reason-text">${escapeHtml(issue.reason || '')}</div>
                                ${labels.length ? `<div class="labels-row">${labels.map(l => `<span class="label-chip">${escapeHtml(l)}</span>`).join('')}</div>` : ''}
                            </div>
                        </div>
                        <div class="issue-detail">${detailHtml}</div>
                    </div>`;
            }).join('');

            renderPagination(totalPages);
        }

        function renderPagination(totalPages) {
            if (totalPages <= 1) {
                document.getElementById('pagination').innerHTML = '';
                return;
            }
            let btns = '';
            btns += `<button class="page-btn" onclick="goPage(${currentPage - 1})" ${currentPage === 1 ? 'disabled' : ''}>‹ Prev</button>`;

            const range = getPageRange(currentPage, totalPages);
            range.forEach(p => {
                if (p === '...') {
                    btns += `<span class="page-info">…</span>`;
                } else {
                    btns += `<button class="page-btn ${p === currentPage ? 'active' : ''}" onclick="goPage(${p})">${p}</button>`;
                }
            });

            btns += `<button class="page-btn" onclick="goPage(${currentPage + 1})" ${currentPage === totalPages ? 'disabled' : ''}>Next ›</button>`;
            document.getElementById('pagination').innerHTML = btns;
        }

        function getPageRange(current, total) {
            if (total <= 7) return Array.from({length: total}, (_, i) => i + 1);
            if (current <= 3) return [1, 2, 3, 4, '...', total];
            if (current >= total - 2) return [1, '...', total - 3, total - 2, total - 1, total];
            return [1, '...', current - 1, current, current + 1, '...', total];
        }

        function toggleCard(idx) {
            const card = document.getElementById(`card-${idx}`);
            const wasExpanded = card.classList.contains('expanded');
            card.classList.toggle('expanded');
            const hint = card.querySelector('.expand-hint');
            if (hint) hint.textContent = wasExpanded ? '▼ click to expand' : '▲ click to collapse';
        }

        function goPage(page) {
            const totalPages = Math.ceil(filteredIssues.length / pageSize);
            currentPage = Math.max(1, Math.min(page, totalPages));
            renderIssues();
            window.scrollTo({ top: 200, behavior: 'smooth' });
        }

        function escapeHtml(text) {
            const div = document.createElement('div');
            div.textContent = text || '';
            return div.innerHTML;
        }

        function formatMarkdown(text) {
            if (!text) return '';
            return text
                .replace(/&/g, '&amp;')
                .replace(/</g, '&lt;')
                .replace(/>/g, '&gt;')
                .replace(/^## (.+)$/gm, '<h2>$1</h2>')
                .replace(/^### (.+)$/gm, '<h3>$1</h3>')
                .replace(/\\*\\*(.+?)\\*\\*/g, '<strong>$1</strong>')
                .replace(/`([^`]+)`/g, '<code>$1</code>')
                .replace(/^- (.+)$/gm, '<li>$1</li>')
                .replace(/(<li>.*<\\/li>)/gms, '<ul>$1</ul>')
                .replace(/^(\\d+)\\.\\s(.+)$/gm, '<li>$2</li>')
                .replace(/\\n\\n/g, '</p><p>')
                .replace(/\\n/g, '<br>');
        }

        function exportCSV() {
            const headers = ['title', 'url', 'repo', 'gfi_score', 'reason', 'labels', 'comments', 'created_at'];
            const rows = filteredIssues.map(i => headers.map(h => `"${(i[h] || '').toString().replace(/"/g, '""')}"`).join(','));
            const csv = headers.join(',') + '\\n' + rows.join('\\n');
            const blob = new Blob([csv], {type: 'text/csv'});
            const url = URL.createObjectURL(blob);
            const a = document.createElement('a');
            a.href = url;
            a.download = 'filtered_gfis.csv';
            a.click();
            URL.revokeObjectURL(url);
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
