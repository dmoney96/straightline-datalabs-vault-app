#!/usr/bin/env python
from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path
import sys
import re
import io
import csv
import os
from urllib.parse import urlparse, parse_qs, unquote

import requests
from bs4 import BeautifulSoup
from flask import (
    Flask,
    request,
    render_template_string,
    abort,
)

# Optional DOCX support
try:
    from docx import Document  # type: ignore[import]
except ImportError:  # pragma: no cover
    Document = None

# ---------- Path / imports ----------

# Project root (directory that contains vault_core)
ROOT = Path(__file__).resolve().parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from vault_core.manifest import iter_manifest, append_manifest_entry  # type: ignore[import]
from vault_core.ingest.pipeline import ingest_source  # type: ignore[import]
from vault_core.paths import DATA_DIR, OCR_DIR  # type: ignore[import]

app = Flask(__name__)
app.secret_key = os.getenv("STRAIGHTLINE_SECRET_KEY", "dev-not-secret")


# ---------- Debug helper ----------

def _log_debug(msg: str) -> None:
    """Simple stderr logger so messages show up in journalctl."""
    print(f"WEBDEBUG: {msg}", file=sys.stderr, flush=True)


# ---------- Web search helpers (DuckDuckGo HTML) ----------

DUCKDUCKGO_SEARCH_URL = "https://duckduckgo.com/html/"


def _extract_real_url(href: str) -> str:
    """
    DuckDuckGo often wraps outbound links as /l/?uddg=<encoded_url>
    OR https://duckduckgo.com/l/?uddg=<encoded_url>.
    This helper unwraps both styles when present.
    """
    parsed = urlparse(href)

    # Case 1: relative wrapper like /l/?uddg=...
    if not parsed.scheme and href.startswith("/"):
        qs = parse_qs(parsed.query)
        if "uddg" in qs and qs["uddg"]:
            return unquote(qs["uddg"][0])
        return href

    # Case 2: absolute wrapper like https://duckduckgo.com/l/?uddg=...
    if parsed.netloc and "duckduckgo.com" in parsed.netloc and parsed.path.startswith("/l"):
        qs = parse_qs(parsed.query)
        if "uddg" in qs and qs["uddg"]:
            return unquote(qs["uddg"][0])

    # Otherwise just return original href
    return href


def fetch_doc_urls(query: str, limit: int = 5) -> list[str]:
    """
    Use DuckDuckGo's HTML endpoint to find document-like URLs.
    Let ingest_url_web decide how to treat each URL.
    """
    _log_debug(f"fetch_doc_urls: query={query!r}, limit={limit}")

    params = {"q": query, "kl": "us-en"}
    headers = {
        "User-Agent": (
            "Mozilla/5.0 (X11; Linux x86_64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/120.0 Safari/537.36 StraightlineVault/0.1"
        ),
        "Accept-Language": "en-US,en;q=0.9",
    }

    def _parse_response(resp: requests.Response, label: str) -> list[str]:
        _log_debug(f"{label}: status={resp.status_code}, final_url={resp.url}")
        resp.raise_for_status()

        snippet = resp.text[:400].replace("\n", "\\n")
        _log_debug(f"{label}: body_snippet={snippet}")

        soup = BeautifulSoup(resp.text, "html.parser")
        anchors = soup.find_all("a", href=True)
        _log_debug(f"{label}: total_anchors={len(anchors)}")

        urls: list[str] = []
        debug_shown = 0

        for a in anchors:
            raw_href = a["href"]
            href = _extract_real_url(raw_href)

            # Skip non-http links
            if not href.startswith("http"):
                continue

            netloc = urlparse(href).netloc.lower()

            # Skip DDG internal links after unwrap
            if "duckduckgo.com" in netloc:
                continue

            if href not in urls:
                urls.append(href)
                if debug_shown < 10:
                    _log_debug(f"{label}: candidate_url={href}")
                    debug_shown += 1

            if len(urls) >= limit:
                break

        _log_debug(f"{label}: returning {len(urls)} url(s)")
        return urls

    urls: list[str] = []

    # Try POST to html.duckduckgo.com/html first
    try:
        resp_post = requests.post(
            "https://html.duckduckgo.com/html/",
            data=params,
            headers=headers,
            timeout=20,
        )
        urls = _parse_response(resp_post, "ddg_post")
    except Exception as e:
        _log_debug(f"ddg_post_error={e!r}")

    # Fallback: GET on duckduckgo.com/html
    if not urls:
        try:
            resp_get = requests.get(
                DUCKDUCKGO_SEARCH_URL,
                params=params,
                headers=headers,
                timeout=20,
            )
            urls = _parse_response(resp_get, "ddg_get")
        except Exception as e:
            _log_debug(f"ddg_get_error={e!r}")

    _log_debug(f"fetch_doc_urls: final_urls={len(urls)}")
    return urls[:limit]


# ---------- Ingest helpers for ANY URL ----------

def _slug_from_url(url: str) -> str:
    """Turn a URL into a filesystem-safe slug for TXT filenames."""
    parsed = urlparse(url)
    base = (parsed.netloc + parsed.path).lower()
    slug = re.sub(r"[^a-z0-9]+", "_", base).strip("_")
    return slug or "web_doc"


def _write_txt_and_manifest(
    text: str,
    url: str,
    case: str | None,
    kind: str,
) -> Path:
    """
    Write text into OCR_DIR as a .txt file and append a manifest entry.
    Returns the txt_path.
    """
    OCR_DIR.mkdir(parents=True, exist_ok=True)

    slug = _slug_from_url(url)
    txt_path = OCR_DIR / f"{slug}.txt"
    txt_path.write_text(text, encoding="utf-8")

    # Store txt path relative to DATA_DIR if possible (keeps vault relocatable)
    try:
        txt_rel = txt_path.relative_to(DATA_DIR)
    except ValueError:
        txt_rel = txt_path

    entry: dict[str, object | None] = {
        "kind": kind,
        "pdf": None,
        "txt": str(txt_rel),
        "source_url": url,
    }
    if case:
        entry["case"] = case

    append_manifest_entry(entry)
    return txt_path


def _extract_docx_text(content: bytes) -> str:
    """Extract plain text from DOCX (in-memory bytes) using python-docx."""
    if Document is None:
        raise RuntimeError(
            "python-docx is not installed. Install it with 'pip install python-docx'."
        )

    with io.BytesIO(content) as buf:
        doc = Document(buf)
    parts: list[str] = []
    for para in doc.paragraphs:
        text = para.text.strip()
        if text:
            parts.append(text)
    return "\n".join(parts)


def _extract_csv_text(text: str) -> str:
    """Flatten CSV text into a readable, line-based text block for indexing."""
    out_lines: list[str] = []
    reader = csv.reader(text.splitlines())
    for row in reader:
        out_lines.append("\t".join(cell.strip() for cell in row))
    return "\n".join(out_lines)


def ingest_url_web(url: str, case: str | None):
    """
    Ingest an arbitrary URL:

      - If it's a PDF -> delegate to ingest_source (existing pipeline).
      - If it's DOCX -> extract text via python-docx -> web_docx.
      - If it's CSV -> flatten rows -> web_csv.
      - If it's HTML -> strip to text and record as web_html.
      - If it's text/* -> record as web_text.

    Returns (pdf_path, txt_path) where pdf_path may be None.
    """
    headers = {
        "User-Agent": "Mozilla/5.0 (StraightlineVault/0.1; +non-malicious investigative use)",
    }

    # Try HEAD first to inspect content-type
    try:
        head_resp = requests.head(
            url,
            headers=headers,
            timeout=15,
            allow_redirects=True,
        )
        ctype = (head_resp.headers.get("Content-Type") or "").lower()
    except Exception:
        head_resp = None
        ctype = ""

    # PDF: existing pipeline
    if "pdf" in ctype or url.lower().endswith(".pdf"):
        pdf_path, txt_path = ingest_source(url, case=case)
        return pdf_path, txt_path

    # Fetch body for everything else
    resp = requests.get(url, headers=headers, timeout=30)
    resp.raise_for_status()
    if not ctype:
        ctype = (resp.headers.get("Content-Type") or "").lower()

    # DOCX
    if (
        "officedocument.wordprocessingml.document" in ctype
        or url.lower().endswith(".docx")
    ):
        text = _extract_docx_text(resp.content)
        txt_path = _write_txt_and_manifest(text, url, case, kind="web_docx")
        return None, txt_path

    # CSV
    if "text/csv" in ctype or url.lower().endswith(".csv"):
        raw_text = resp.text
        text = _extract_csv_text(raw_text)
        txt_path = _write_txt_and_manifest(text, url, case, kind="web_csv")
        return None, txt_path

    # HTML
    if "html" in ctype or url.lower().endswith((".htm", ".html", "/")):
        soup = BeautifulSoup(resp.text, "html_parser" if False else "html.parser")
        text = soup.get_text("\n", strip=True)
        txt_path = _write_txt_and_manifest(text, url, case, kind="web_html")
        return None, txt_path

    # Generic text/*
    if ctype.startswith("text/"):
        text = resp.text
        txt_path = _write_txt_and_manifest(text, url, case, kind="web_text")
        return None, txt_path

    # Fallback
    raise ValueError(f"Unsupported content type for web ingest: {ctype or 'unknown'}")


# ---------- Base Styles (responsive + dark mode) ----------

BASE_STYLE = """
<style>
  :root {
    color-scheme: dark light;
    --bg: #0b0c10;
    --bg-elevated: #11141c;
    --border-subtle: #252839;
    --text-main: #e4e7f5;
    --text-muted: #9ca3c7;
    --accent: #60a5fa;
    --accent-soft: rgba(96, 165, 250, 0.15);
    --danger: #f97373;
    --code-bg: #111827;
    --chip-bg: #1f2937;
    --radius-lg: 12px;
    --radius-pill: 999px;
    --shadow-soft: 0 18px 40px rgba(15, 23, 42, 0.45);
  }

  @media (prefers-color-scheme: light) {
    :root {
      --bg: #f3f4f8;
      --bg-elevated: #ffffff;
      --border-subtle: #d1d5e5;
      --text-main: #111827;
      --text-muted: #6b7280;
      --accent: #2563eb;
      --accent-soft: rgba(37, 99, 235, 0.08);
      --danger: #b91c1c;
      --code-bg: #f3f4f6;
      --chip-bg: #eef2ff;
      --shadow-soft: 0 18px 40px rgba(148, 163, 184, 0.45);
    }
  }

  * {
    box-sizing: border-box;
  }

  body {
    margin: 0;
    min-height: 100vh;
    font-family: system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
    background: radial-gradient(circle at top left, #111827 0, #020617 45%, #000 100%);
    color: var(--text-main);
    display: flex;
    justify-content: center;
    padding: 2.5rem 1rem;
  }

  .page-shell {
    width: 100%;
    max-width: 1100px;
    background: linear-gradient(145deg, rgba(15,23,42,0.98), rgba(17,24,39,0.98));
    border-radius: 18px;
    border: 1px solid rgba(148,163,184,0.2);
    box-shadow: var(--shadow-soft);
    padding: 1.75rem 1.5rem 2rem;
  }

  @media (min-width: 900px) {
    .page-shell {
      padding: 2rem 2.25rem 2.25rem;
    }
  }

  header {
    display: flex;
    flex-wrap: wrap;
    align-items: baseline;
    justify-content: space-between;
    row-gap: 0.25rem;
    margin-bottom: 1.25rem;
  }

  h1 {
    font-size: 1.5rem;
    letter-spacing: 0.04em;
    text-transform: uppercase;
    margin: 0;
    display: flex;
    align-items: center;
    gap: 0.4rem;
  }

  h1 span.badge {
    font-size: 0.7rem;
    text-transform: uppercase;
    letter-spacing: 0.12em;
    padding: 0.18rem 0.6rem;
    border-radius: var(--radius-pill);
    background: var(--accent-soft);
    color: var(--accent);
    border: 1px solid rgba(96,165,250,0.4);
  }

  h2 {
    margin-top: 1.5rem;
    margin-bottom: 0.5rem;
    font-size: 1rem;
    text-transform: uppercase;
    letter-spacing: 0.09em;
    color: var(--text-muted);
  }

  a {
    color: inherit;
    text-decoration: none;
  }
  a:hover {
    text-decoration: underline;
  }

  nav {
    display: flex;
    gap: 0.4rem;
    flex-wrap: wrap;
  }

  nav a {
    font-size: 0.8rem;
    padding: 0.3rem 0.8rem;
    border-radius: var(--radius-pill);
    border: 1px solid rgba(148,163,184,0.35);
    background: rgba(15,23,42,0.7);
    color: var(--text-muted);
    text-decoration: none;
  }

  nav a:hover {
    border-color: var(--accent);
    color: var(--accent);
    background: rgba(15,23,42,0.95);
  }

  .subtitle {
    font-size: 0.82rem;
    color: var(--text-muted);
    margin-top: 0.3rem;
  }

  form.search-form,
  form.web-form {
    margin: 1.3rem 0 1.4rem;
    display: flex;
    flex-direction: column;
    gap: 0.6rem;
  }

  .field-row {
    display: flex;
    gap: 0.6rem;
    align-items: center;
    flex-wrap: wrap;
  }

  label {
    font-size: 0.8rem;
    color: var(--text-muted);
    min-width: 3.5rem;
  }

  input[type="text"],
  input[type="number"] {
    flex: 1 1 180px;
    min-width: 0;
    padding: 0.5rem 0.65rem;
    border-radius: 999px;
    border: 1px solid var(--border-subtle);
    background: rgba(15,23,42,0.9);
    color: var(--text-main);
    font-size: 0.88rem;
  }

  input[type="text"]::placeholder {
    color: rgba(148,163,184,0.8);
  }

  input[type="text"]:focus,
  input[type="number"]:focus {
    outline: none;
    border-color: var(--accent);
    box-shadow: 0 0 0 1px rgba(96,165,250,0.45);
  }

  button[type="submit"] {
    border: none;
    border-radius: 999px;
    padding: 0.5rem 0.9rem;
    font-size: 0.85rem;
    background: radial-gradient(circle at 0 0, #60a5fa, #2563eb);
    color: #f9fafb;
    cursor: pointer;
    display: inline-flex;
    align-items: center;
    gap: 0.3rem;
    white-space: nowrap;
  }

  button[type="submit"]:hover {
    filter: brightness(1.05);
  }

  button[type="submit"]::after {
    content: "↵";
    font-size: 0.9rem;
    opacity: 0.9;
  }

  .meta {
    color: var(--text-muted);
    font-size: 0.8rem;
  }

  .results-list {
    margin-top: 0.4rem;
  }

  .result {
    border-radius: var(--radius-lg);
    border: 1px solid var(--border-subtle);
    padding: 0.7rem 0.75rem;
    margin-top: 0.6rem;
    background: radial-gradient(circle at top left, #020617 0, #020617 45%, #020617 100%);
  }

  .result-header {
    display: flex;
    justify-content: space-between;
    align-items: baseline;
    gap: 0.4rem;
  }

  .result-title a {
    font-weight: 600;
    letter-spacing: 0.02em;
  }

  .score {
    font-size: 0.75rem;
    color: var(--text-muted);
  }

  .chip-row {
    margin-top: 0.35rem;
    display: flex;
    flex-wrap: wrap;
    gap: 0.3rem;
  }

  .chip {
    font-size: 0.7rem;
    padding: 0.12rem 0.55rem;
    border-radius: var(--radius-pill);
    background: var(--chip-bg);
    color: var(--text-muted);
  }

  .chip strong {
    font-weight: 500;
    color: var(--text-main);
  }

  .snippet {
    margin-top: 0.4rem;
    font-size: 0.85rem;
    color: #e5e7eb;
  }

  code {
    font-family: ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas,
      "Liberation Mono", "Courier New", monospace;
    font-size: 0.78rem;
    background: var(--code-bg);
    padding: 0.1rem 0.4rem;
    border-radius: 999px;
  }

  table {
    border-collapse: collapse;
    width: 100%;
    font-size: 0.85rem;
    margin-top: 0.75rem;
  }

  th, td {
    border-bottom: 1px solid rgba(148,163,184,0.35);
    padding: 0.4rem 0.25rem;
    text-align: left;
  }

  th {
    font-weight: 600;
    color: var(--text-muted);
  }

  pre {
    white-space: pre-wrap;
    font-family: ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas,
      "Liberation Mono", "Courier New", monospace;
    font-size: 0.85rem;
    background: var(--code-bg);
    padding: 0.75rem 0.9rem;
    border-radius: 12px;
    border: 1px solid rgba(148,163,184,0.35);
    margin-top: 0.7rem;
    max-height: 80vh;
    overflow-y: auto;
  }

  .error {
    color: var(--danger);
    margin-top: 0.5rem;
    font-size: 0.85rem;
  }

  .ingested-item {
    border-radius: var(--radius-lg);
    border: 1px solid var(--border-subtle);
    padding: 0.6rem 0.7rem;
    margin-top: 0.6rem;
    background: rgba(15,23,42,0.9);
  }

  .ingested-item strong {
    font-weight: 600;
  }

  .section-card {
    margin-top: 0.75rem;
    padding: 0.7rem 0.8rem;
    border-radius: var(--radius-lg);
    border: 1px dashed rgba(148,163,184,0.45);
    background: rgba(15,23,42,0.6);
  }

  @media (max-width: 640px) {
    .page-shell {
      padding: 1.25rem 1rem 1.5rem;
      border-radius: 0;
      border-left: none;
      border-right: none;
    }

    header {
      flex-direction: column;
      align-items: flex-start;
      gap: 0.4rem;
    }
  }
</style>
"""


# ---------- Templates ----------

INDEX_TEMPLATE = """
<!doctype html>
<html><head>
<meta charset="utf-8"><title>Straightline Vault Search</title>
{{ style|safe }}
</head>
<body>
  <main class="page-shell">
    <header>
      <div>
        <h1>Straightline Vault <span class="badge">Search</span></h1>
        <p class="subtitle">Full-text search across your ingested corpus. Protected by nginx basic auth.</p>
      </div>
      <nav>
        <a href="/">Search</a>
        <a href="/cases">Cases</a>
        <a href="/web-ingest">Web Ingest</a>
      </nav>
    </header>

    <form method="get" action="/" class="search-form">
      <div class="field-row">
        <label for="q">Query</label>
        <input type="text" id="q" name="q" value="{{ q|e }}" autofocus placeholder="epstein subpoena maxwell 1320...">
      </div>
      <div class="field-row">
        <label for="case">Case</label>
        <input type="text" id="case" name="case" value="{{ case|e }}" placeholder="optional (e.g. maxwell_1320)">
      </div>
      <div class="field-row">
        <label for="kind">Kind</label>
        <input type="text" id="kind" name="kind" value="{{ kind|e }}" placeholder="local_file, url_fetch, web_html">
      </div>
      <div class="field-row">
        <label for="limit">Limit</label>
        <input type="number" id="limit" name="limit" value="{{ limit }}">
        <button type="submit">Search</button>
      </div>
    </form>

    {% if q %}
      <section class="section-card">
        <div class="meta">
          <strong>Query:</strong> <code>{{ q }}</code>
          {% if case %} · <strong>Case:</strong> <code>{{ case }}</code>{% endif %}
          {% if kind %} · <strong>Kind:</strong> <code>{{ kind }}</code>{% endif %}
        </div>
      </section>

      <h2>Results</h2>
      {% if results %}
        <p class="meta">{{ results|length }} result(s) shown.</p>
        <div class="results-list">
          {% for r in results %}
            <article class="result">
              <div class="result-header">
                <div class="result-title">
                  <strong><a href="/doc/{{ r.doc_id }}">{{ r.doc_id }}</a></strong>
                </div>
                <span class="score">score={{ "%.2f"|format(r.score) }}</span>
              </div>
              <div class="chip-row">
                <span class="chip"><strong>source</strong> <code>{{ r.source }}</code></span>
                {% if r.case %}
                  <span class="chip"><strong>case</strong> {{ r.case }}</span>
                {% endif %}
                {% if r.kind %}
                  <span class="chip"><strong>kind</strong> {{ r.kind }}</span>
                {% endif %}
              </div>
              <p class="snippet">{{ r.snippet|safe }}</p>
            </article>
          {% endfor %}
        </div>
      {% else %}
        <p class="meta">No results found.</p>
      {% endif %}
    {% endif %}
  </main>
</body></html>
"""

CASES_TEMPLATE = """
<!doctype html>
<html><head>
<meta charset="utf-8"><title>Straightline Vault — Cases</title>
{{ style|safe }}
</head>
<body>
  <main class="page-shell">
    <header>
      <div>
        <h1>Straightline Vault <span class="badge">Cases</span></h1>
        <p class="subtitle">Overview of all cases represented in the manifest.</p>
      </div>
      <nav>
        <a href="/">Search</a>
        <a href="/cases">Cases</a>
        <a href="/web-ingest">Web Ingest</a>
      </nav>
    </header>

    {% if not cases %}
      <p class="meta">No cases found.</p>
    {% else %}
      <h2>Case Index</h2>
      <table>
        <thead>
          <tr><th>Case</th><th>Total docs</th><th>Kind breakdown</th></tr>
        </thead>
        <tbody>
          {% for case_name, info in cases %}
            <tr>
              <td><a href="/case/{{ case_name }}">{{ case_name }}</a></td>
              <td>{{ info.total }}</td>
              <td>
                {% for kind, count in info.kinds.items() %}
                  {{ kind }}={{ count }}{% if not loop.last %}, {% endif %}
                {% endfor %}
              </td>
            </tr>
          {% endfor %}
        </tbody>
      </table>
    {% endif %}
  </main>
</body></html>
"""

CASE_TEMPLATE = """
<!doctype html>
<html><head>
<meta charset="utf-8"><title>Straightline Vault — Case {{ case_name }}</title>
{{ style|safe }}
</head>
<body>
  <main class="page-shell">
    <header>
      <div>
        <h1>Straightline Vault <span class="badge">Case</span></h1>
        <p class="subtitle">Documents for case <code>{{ case_name }}</code>.</p>
      </div>
      <nav>
        <a href="/">Search</a>
        <a href="/cases">Cases</a>
        <a href="/web-ingest">Web Ingest</a>
      </nav>
    </header>

    <p class="meta">
      {{ docs|length }} document(s) in this case —
      <a href="/?case={{ case_name|e }}">Search within this case</a>
    </p>

    {% if not docs %}
      <p class="meta">No documents found for this case.</p>
    {% else %}
      <h2>Documents</h2>
      <table>
        <thead>
          <tr><th>Doc ID</th><th>Kind</th><th>PDF</th><th>Source URL</th></tr>
        </thead>
        <tbody>
          {% for d in docs %}
            <tr>
              <td><a href="/doc/{{ d.doc_id }}">{{ d.doc_id }}</a></td>
              <td>{{ d.kind or "" }}</td>
              <td><code>{{ d.pdf or "" }}</code></td>
              <td>{% if d.source_url %}<code>{{ d.source_url }}</code>{% endif %}</td>
            </tr>
          {% endfor %}
        </tbody>
      </table>
    {% endif %}
  </main>
</body></html>
"""

DOC_TEMPLATE = """
<!doctype html>
<html><head>
<meta charset="utf-8">
<title>Straightline Vault — {{ doc_id }}</title>
{{ style|safe }}
</head>
<body>
  <main class="page-shell">
    <header>
      <div>
        <h1>Straightline Vault <span class="badge">Document</span></h1>
        <p class="subtitle">Inspect OCR text for a single ingested document.</p>
      </div>
      <nav>
        <a href="/">Search</a>
        <a href="/cases">Cases</a>
        {% if case_name %}
          <a href="/case/{{ case_name }}">Back to case</a>
        {% endif %}
        <a href="/web-ingest">Web Ingest</a>
      </nav>
    </header>

    <section class="section-card">
      <p class="meta">
        <strong>ID:</strong> <code>{{ doc_id }}</code>
        {% if case_name %} · <strong>case</strong>=<code>{{ case_name }}</code>{% endif %}
        {% if kind %} · <strong>kind</strong>=<code>{{ kind }}</code>{% endif %}
        {% if pdf %} · <strong>PDF:</strong> <code>{{ pdf }}</code>{% endif %}
        {% if source_url %} · <strong>Source URL:</strong> <code>{{ source_url }}</code>{% endif %}
      </p>
    </section>

    <h2>OCR Text</h2>
    {% if error %}
      <p class="meta">Error reading OCR text: {{ error }}</p>
    {% else %}
      <pre>{{ content }}</pre>
    {% endif %}
  </main>
</body></html>
"""

WEB_INGEST_TEMPLATE = """
<!doctype html>
<html><head>
<meta charset="utf-8">
<title>Straightline Vault — Web Ingest</title>
{{ style|safe }}
</head>
<body>
  <main class="page-shell">
    <header>
      <div>
        <h1>Straightline Vault <span class="badge">Web Ingest</span></h1>
        <p class="subtitle">
          Search the public web for documents (PDF, HTML, text, DOCX, CSV) and ingest them into a case.
          PDFs go through the existing pipeline; other types are converted to text and stored.
        </p>
      </div>
      <nav>
        <a href="/">Search</a>
        <a href="/cases">Cases</a>
        <a href="/web-ingest">Web Ingest</a>
      </nav>
    </header>

    <form method="post" action="/web-ingest" class="web-form">
      <div class="field-row">
        <label for="q">Query</label>
        <input type="text" id="q" name="q" value="{{ query|e }}"
               placeholder="e.g. Jamal Khashoggi CIA report site:gov pdf">
      </div>

      <div class="field-row">
        <label for="case">Case</label>
        <input type="text" id="case" name="case" value="{{ case|e }}" placeholder="auto if blank">
      </div>

      <div class="field-row">
        <label for="limit">Limit</label>
        <input type="number" id="limit" name="limit" value="{{ limit }}">
        <button type="submit">Search &amp; Ingest</button>
      </div>

      {% if error %}
        <div class="error">{{ error }}</div>
      {% endif %}
    </form>

    {% if pdf_urls %}
      <h2>URLs Found</h2>
      <div class="section-card">
        <ul class="meta">
          {% for u in pdf_urls %}
            <li><code>{{ u }}</code></li>
          {% endfor %}
        </ul>
      </div>
    {% endif %}

    {% if ingested %}
      <h2>Ingest Results</h2>
      <p class="meta">Case: <strong>{{ case }}</strong></p>
      {% for item in ingested %}
        <div class="ingested-item">
          <div><strong>URL:</strong> <code>{{ item.url }}</code></div>
          {% if item.error %}
            <div class="error"><strong>Error:</strong> {{ item.error }}</div>
          {% else %}
            {% if item.pdf_path %}
              <div class="meta">PDF: <code>{{ item.pdf_path }}</code></div>
            {% endif %}
            {% if item.txt_path %}
              <div class="meta">TXT: <code>{{ item.txt_path }}</code></div>
            {% endif %}
            {% if not item.pdf_path and not item.txt_path %}
              <div class="meta">No paths recorded (unexpected).</div>
            {% endif %}
          {% endif %}
        </div>
      {% endfor %}
    {% endif %}
  </main>
</body></html>
"""


# ---------- Data helpers ----------

def build_case_stats():
    stats = defaultdict(lambda: {"total": 0, "kinds": defaultdict(int)})
    for rec in iter_manifest() or []:
        case = rec.get("case") or "uncategorized"
        kind = rec.get("kind") or "unknown"
        stats[case]["total"] += 1
        stats[case]["kinds"][kind] += 1
    return sorted(
        ((name, {"total": info["total"], "kinds": dict(info["kinds"])})
         for name, info in stats.items()),
        key=lambda x: x[0],
    )


def find_manifest_by_doc_id(doc_id: str):
    for rec in iter_manifest() or []:
        txt = rec.get("txt")
        if not txt:
            continue
        p = Path(txt)
        if p.stem == doc_id:
            return rec
    return None


def load_ocr_text(path_str: str):
    """
    Try multiple resolution strategies so older manifest paths still work:
      - absolute path as-is
      - relative to DATA_DIR
      - relative to ROOT
    """
    try:
        p = Path(path_str)
        if not p.is_absolute():
            # Try DATA_DIR first
            candidate = DATA_DIR / p
            if candidate.exists():
                p = candidate
            else:
                # Fallback: relative to project root
                candidate2 = ROOT / p
                if candidate2.exists():
                    p = candidate2

        text = p.read_text(encoding="utf-8", errors="replace")
        return text, None
    except Exception as e:
        return None, str(e)


# ---------- Simple built-in search ----------

@dataclass
class SimpleResult:
    doc_id: str
    source: str
    case: str | None
    kind: str | None
    snippet: str
    score: float


def simple_search(q: str, case: str | None, kind: str | None, limit: int = 20) -> list[SimpleResult]:
    """
    Very literal full-text search over all TXT files in manifest.

    - Case-insensitive
    - Splits query into tokens on whitespace
    - A doc matches if it contains *any* token (OR semantics)
    - Score = number of distinct tokens that matched + total occurrences
    """
    q = q.strip()
    if not q:
        return []

    tokens = [t.lower() for t in re.split(r"\s+", q) if t.strip()]
    if not tokens:
        return []

    results: list[SimpleResult] = []

    all_recs = list(iter_manifest() or [])
    _log_debug(f"simple_search: q={q!r}, tokens={tokens}, manifest_count={len(all_recs)}")

    for rec in all_recs:
        rec_case = rec.get("case") or None
        rec_kind = rec.get("kind") or None

        if case and rec_case != case:
            continue
        if kind and rec_kind != kind:
            continue

        txt_path = rec.get("txt")
        if not txt_path:
            continue

        content, err = load_ocr_text(txt_path)
        if err or not content:
            continue

        content_lower = content.lower()

        matched_tokens: list[str] = []
        total_hits = 0

        for t in tokens:
            if t in content_lower:
                matched_tokens.append(t)
                total_hits += content_lower.count(t)

        if not matched_tokens:
            continue

        # Build a snippet around the first matched token
        first = matched_tokens[0]
        idx = content_lower.find(first)
        if idx == -1:
            snippet = content[:240]
        else:
            start = max(0, idx - 120)
            end = min(len(content), idx + 120)
            snippet = content[start:end]

        snippet = snippet.replace("\n", " ").replace("\r", " ")
        snippet = re.sub(r"\s+", " ", snippet).strip()

        raw_source = rec.get("source_url") or rec.get("pdf") or "(local)"

        # Make local filesystem paths less ugly in the UI
        if isinstance(raw_source, str) and raw_source.startswith("/"):
            source = f"local:{Path(raw_source).name}"
        else:
            source = raw_source

        score = float(len(matched_tokens) + total_hits / 10.0)

        p = Path(txt_path)
        results.append(
            SimpleResult(
                doc_id=p.stem,
                source=str(source),
                case=rec_case,
                kind=rec_kind,
                snippet=snippet,
                score=score,
            )
        )

    # Sort by score descending, then doc_id
    results.sort(key=lambda r: (-r.score, r.doc_id))
    _log_debug(f"simple_search: returning {len(results[:limit])} result(s)")
    return results[:limit]


# ---------- Routes ----------

@app.route("/", methods=["GET"])
def index():
    """
    Main search page — always uses simple_search()
    so results work even if external search backends change.
    """
    q = request.args.get("q", "").strip()
    case = request.args.get("case", "").strip() or None
    kind = request.args.get("kind", "").strip() or None
    limit_str = request.args.get("limit", "") or "20"

    try:
        limit = int(limit_str)
    except ValueError:
        limit = 20

    results = simple_search(q, case=case, kind=kind, limit=limit) if q else []

    return render_template_string(
        INDEX_TEMPLATE,
        style=BASE_STYLE,
        q=q,
        case=case or "",
        kind=kind or "",
        limit=limit,
        results=results,
    )


@app.route("/cases", methods=["GET"])
def cases_view():
    return render_template_string(
        CASES_TEMPLATE,
        style=BASE_STYLE,
        cases=build_case_stats(),
    )


@app.route("/case/<case_name>", methods=["GET"])
def case_view(case_name: str):
    docs = []
    for rec in iter_manifest() or []:
        if (rec.get("case") or "uncategorized") != case_name:
            continue

        txt = rec.get("txt")
        pdf = rec.get("pdf")
        source_url = rec.get("source_url")

        if not txt:
            continue

        p = Path(txt)
        docs.append(
            {
                "doc_id": p.stem,
                "kind": rec.get("kind"),
                "pdf": pdf,
                "source_url": source_url,
            }
        )

    return render_template_string(
        CASE_TEMPLATE,
        style=BASE_STYLE,
        case_name=case_name,
        docs=docs,
    )


@app.route("/doc/<doc_id>", methods=["GET"])
def doc_view(doc_id: str):
    rec = find_manifest_by_doc_id(doc_id)
    if not rec:
        abort(404, description=f"No manifest record found for doc_id={doc_id!r}")

    txt_path = rec.get("txt")
    content, error = load_ocr_text(txt_path) if txt_path else (None, "TXT path missing.")

    return render_template_string(
        DOC_TEMPLATE,
        style=BASE_STYLE,
        doc_id=doc_id,
        case_name=rec.get("case"),
        kind=rec.get("kind"),
        pdf=rec.get("pdf"),
        source_url=rec.get("source_url"),
        content=content,
        error=error,
    )


@app.route("/web-ingest", methods=["GET", "POST"])
def web_ingest():
    """
    Web-ingest is available to *any* nginx-authenticated user.
    Nginx basic auth is the real gate; Flask does not ask for another login.
    """
    query = ""
    case = ""
    limit = 5
    error: str | None = None
    pdf_urls: list[str] = []
    ingested: list[dict] = []

    if request.method == "POST":
        query = (request.form.get("q") or "").strip()
        case = (request.form.get("case") or "").strip()
        limit_raw = (request.form.get("limit") or "").strip()

        try:
            limit = int(limit_raw) if limit_raw else 5
        except ValueError:
            error = "Limit must be an integer."
            limit = 5

        if not error:
            if not query:
                error = "Query is required."
            else:
                # Auto-generate case if blank
                if not case:
                    slug = re.sub(r"[^a-z0-9]+", "_", query.lower()).strip("_")
                    case = f"{slug}_web" if slug else "web"

                try:
                    pdf_urls = fetch_doc_urls(query, limit=limit)
                except Exception as e:
                    error = f"Web search failed: {e}"

                if not error and not pdf_urls:
                    error = "No document-like URLs found in search results."

                if not error:
                    for url in pdf_urls:
                        try:
                            pdf_path, txt_path = ingest_url_web(url, case=case)
                            ingested.append(
                                {
                                    "url": url,
                                    "pdf_path": str(pdf_path) if pdf_path else None,
                                    "txt_path": str(txt_path) if txt_path else None,
                                    "error": None,
                                }
                            )
                        except Exception as e:
                            ingested.append(
                                {
                                    "url": url,
                                    "pdf_path": None,
                                    "txt_path": None,
                                    "error": str(e),
                                }
                            )

    return render_template_string(
        WEB_INGEST_TEMPLATE,
        style=BASE_STYLE,
        query=query,
        case=case,
        limit=limit,
        error=error,
        pdf_urls=pdf_urls,
        ingested=ingested,
    )


if __name__ == "__main__":
    # Dev mode only; production should use gunicorn behind nginx.
    app.run(host="127.0.0.1", port=5001, debug=True)
