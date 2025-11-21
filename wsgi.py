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


# ---------- Base Styles ----------

BASE_STYLE = """
<style>
  :root {
    color-scheme: light dark;
  }

  * {
    box-sizing: border-box;
  }

  body {
    font-family: system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
    margin: 1.5rem auto;
    padding: 0 1.25rem 2rem;
    max-width: 980px;
    line-height: 1.5;
    background: #020617;
    color: #e5e7eb;
  }

  @media (prefers-color-scheme: light) {
    body {
      background: #f9fafb;
      color: #111827;
    }
  }

  h1 {
    margin: 0.5rem 0 0.25rem;
    font-size: 1.6rem;
    letter-spacing: 0.02em;
  }

  h2 {
    margin-top: 1.75rem;
    font-size: 1.2rem;
  }

  a {
    text-decoration: none;
    color: #38bdf8;
  }

  a:hover {
    text-decoration: underline;
  }

  nav {
    display: flex;
    gap: 0.75rem;
    align-items: center;
    margin-bottom: 0.75rem;
    padding: 0.5rem 0;
    border-bottom: 1px solid rgba(148, 163, 184, 0.4);
    position: sticky;
    top: 0;
    backdrop-filter: blur(10px);
    background: linear-gradient(
      to bottom,
      rgba(15, 23, 42, 0.95),
      rgba(15, 23, 42, 0.85),
      transparent
    );
    z-index: 10;
  }

  @media (prefers-color-scheme: light) {
    nav {
      border-bottom-color: rgba(203, 213, 225, 0.7);
      background: linear-gradient(
        to bottom,
        rgba(248, 250, 252, 0.96),
        rgba(248, 250, 252, 0.9),
        transparent
      );
    }
  }

  nav a {
    font-size: 0.95rem;
    padding: 0.25rem 0.5rem;
    border-radius: 999px;
  }

  nav a:hover {
    background: rgba(148, 163, 184, 0.18);
  }

  form {
    margin: 1rem 0 1.5rem;
    padding: 0.85rem 1rem 1rem;
    border-radius: 0.85rem;
    border: 1px solid rgba(148, 163, 184, 0.45);
    background: radial-gradient(circle at top left, rgba(56, 189, 248, 0.12), transparent 55%),
                radial-gradient(circle at bottom right, rgba(129, 140, 248, 0.16), transparent 60%),
                rgba(15, 23, 42, 0.9);
  }

  @media (prefers-color-scheme: light) {
    form {
      background: radial-gradient(circle at top left, rgba(59, 130, 246, 0.06), transparent 55%),
                  radial-gradient(circle at bottom right, rgba(14, 116, 144, 0.08), transparent 60%),
                  #ffffff;
      border-color: rgba(148, 163, 184, 0.5);
    }
  }

  .form-row {
    display: flex;
    flex-wrap: wrap;
    gap: 0.6rem 0.75rem;
    align-items: center;
    margin-bottom: 0.4rem;
  }

  label {
    min-width: 4.5rem;
    font-size: 0.9rem;
    color: #cbd5f5;
  }

  @media (prefers-color-scheme: light) {
    label {
      color: #4b5563;
    }
  }

  input[type="text"],
  input[type="number"] {
    flex: 1 1 10rem;
    max-width: 26rem;
    padding: 0.4rem 0.5rem;
    border-radius: 0.5rem;
    border: 1px solid rgba(148, 163, 184, 0.7);
    background: rgba(15, 23, 42, 0.8);
    color: inherit;
    font-size: 0.95rem;
  }

  @media (prefers-color-scheme: light) {
    input[type="text"],
    input[type="number"] {
      background: #f9fafb;
    }
  }

  input[type="text"]:focus,
  input[type="number"]:focus {
    outline: 2px solid #38bdf8;
    outline-offset: 1px;
    border-color: #38bdf8;
  }

  button[type="submit"] {
    margin-left: 0.5rem;
    padding: 0.45rem 0.9rem;
    border-radius: 999px;
    border: none;
    font-size: 0.9rem;
    cursor: pointer;
    background: linear-gradient(135deg, #38bdf8, #6366f1);
    color: #0b1120;
    font-weight: 600;
    box-shadow: 0 10px 25px rgba(37, 99, 235, 0.35);
  }

  button[type="submit"]:hover {
    filter: brightness(1.05);
    box-shadow: 0 12px 28px rgba(37, 99, 235, 0.45);
  }

  .meta {
    color: #9ca3af;
    font-size: 0.9rem;
  }

  .result {
    border-radius: 0.75rem;
    padding: 0.75rem 0.9rem;
    margin: 0.4rem 0;
    border: 1px solid rgba(31, 41, 55, 0.9);
    background: radial-gradient(circle at top left, rgba(148, 163, 184, 0.16), transparent 55%),
                rgba(15, 23, 42, 0.95);
  }

  @media (prefers-color-scheme: light) {
    .result {
      background: #ffffff;
      border-color: rgba(148, 163, 184, 0.55);
    }
  }

  .result strong a {
    font-size: 0.98rem;
  }

  .snippet {
    margin-top: 0.35rem;
    font-size: 0.95rem;
  }

  .score {
    font-size: 0.82rem;
    color: #9ca3af;
    margin-left: 0.35rem;
  }

  code {
    font-family: ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas,
                 "Liberation Mono", "Courier New", monospace;
    font-size: 0.85rem;
  }

  table {
    border-collapse: collapse;
    width: 100%;
    margin-top: 0.75rem;
    border-radius: 0.75rem;
    overflow: hidden;
    border: 1px solid rgba(55, 65, 81, 0.85);
  }

  th, td {
    border-bottom: 1px solid rgba(55, 65, 81, 0.85);
    padding: 0.45rem 0.45rem;
    text-align: left;
    font-size: 0.9rem;
  }

  th {
    font-weight: 600;
    background: rgba(15, 23, 42, 0.95);
  }

  @media (prefers-color-scheme: light) {
    th, td {
      border-bottom-color: rgba(209, 213, 219, 0.9);
    }
    th {
      background: #f3f4f6;
    }
  }

  pre {
    white-space: pre-wrap;
    font-family: ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas,
                 "Liberation Mono", "Courier New", monospace;
    font-size: 0.9rem;
    padding: 0.75rem 0.9rem;
    border-radius: 0.75rem;
    border: 1px solid rgba(55, 65, 81, 0.9);
    background: rgba(15, 23, 42, 0.95);
    max-height: 70vh;
    overflow: auto;
  }

  @media (prefers-color-scheme: light) {
    pre {
      background: #ffffff;
      border-color: rgba(209, 213, 219, 0.95);
    }
  }

  .error {
    color: #fecaca;
    background: rgba(127, 29, 29, 0.32);
    border-radius: 0.6rem;
    padding: 0.4rem 0.6rem;
    margin-top: 0.5rem;
    font-size: 0.9rem;
  }

  @media (prefers-color-scheme: light) {
    .error {
      color: #7f1d1d;
      background: #fee2e2;
    }
  }

  .ingested-item {
    border-radius: 0.75rem;
    padding: 0.55rem 0.65rem;
    margin: 0.4rem 0;
    border: 1px dashed rgba(55, 65, 81, 0.85);
    background: rgba(15, 23, 42, 0.9);
  }

  @media (prefers-color-scheme: light) {
    .ingested-item {
      background: #f9fafb;
      border-color: rgba(148, 163, 184, 0.8);
    }
  }

  @media (max-width: 640px) {
    body {
      margin-top: 1rem;
      padding-inline: 0.75rem;
    }
    nav {
      flex-wrap: wrap;
      gap: 0.4rem 0.6rem;
    }
    form {
      padding-inline: 0.75rem;
    }
  }
</style>
"""


# ---------- Templates (no internal admin login) ----------

INDEX_TEMPLATE = """
<!doctype html>
<html><head>
<meta charset="utf-8"><title>Straightline Vault Search</title>
{{ style|safe }}
</head>
<body>
<nav>
  <a href="/">Search</a>
  <a href="/cases">Cases</a>
  <a href="/web-ingest">Web Ingest</a>
</nav>

<h1>Straightline Vault</h1>
<p class="meta">Full-text search across your ingested corpus. Protected by nginx basic auth.</p>

<form method="get" action="/">
  <div class="form-row">
    <label for="q">Query</label>
    <input type="text" id="q" name="q" value="{{ q|e }}" autofocus>
  </div>
  <div class="form-row">
    <label for="case">Case</label>
    <input type="text" id="case" name="case" value="{{ case|e }}" placeholder="optional">
  </div>
  <div class="form-row">
    <label for="kind">Kind</label>
    <input type="text" id="kind" name="kind" value="{{ kind|e }}" placeholder="local_file, url_fetch, web_html">
  </div>
  <div class="form-row" style="margin-top: 0.25rem;">
    <label for="limit">Limit</label>
    <input type="number" id="limit" name="limit" value="{{ limit }}">
    <button type="submit">Search</button>
  </div>
</form>

{% if q %}
  <h2>Results for <code>{{ q }}</code></h2>
  {% if results %}
    <p class="meta">{{ results|length }} result(s) shown.</p>
    {% for r in results %}
      <div class="result">
        <div>
          <strong><a href="/doc/{{ r.doc_id }}">{{ r.doc_id }}</a></strong>
          <span class="score">(score={{ "%.2f"|format(r.score) }})</span>
        </div>
        <div class="meta">
          Source:
          <code title="{{ r.source_full or r.source }}">{{ r.source }}</code>
          {% if r.case or r.kind %}
            —
            {% if r.case %}case={{ r.case }}{% endif %}
            {% if r.kind %} kind={{ r.kind }}{% endif %}
          {% endif %}
        </div>
        <div class="snippet">{{ r.snippet|safe }}</div>
      </div>
    {% endfor %}
  {% else %}
    <p>No results found.</p>
  {% endif %}
{% endif %}
</body></html>
"""

CASES_TEMPLATE = """
<!doctype html>
<html><head>
<meta charset="utf-8"><title>Straightline Vault — Cases</title>
{{ style|safe }}
</head>
<body>
<nav>
  <a href="/">Search</a>
  <a href="/cases">Cases</a>
  <a href="/web-ingest">Web Ingest</a>
</nav>

<h1>Cases</h1>
<p class="meta">Overview of all cases in the manifest.</p>

{% if not cases %}
  <p>No cases found.</p>
{% else %}
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
</body></html>
"""

CASE_TEMPLATE = """
<!doctype html>
<html><head>
<meta charset="utf-8"><title>Straightline Vault — Case {{ case_name }}</title>
{{ style|safe }}
</head>
<body>
<nav>
  <a href="/">Search</a>
  <a href="/cases">Cases</a>
  <a href="/web-ingest">Web Ingest</a>
</nav>

<h1>Case: {{ case_name }}</h1>
<p class="meta">
  {{ docs|length }} document(s) in this case.
  —
  <a href="/?case={{ case_name|e }}">Search within this case</a>
</p>

{% if not docs %}
  <p>No documents found for this case.</p>
{% else %}
  <table>
    <thead>
      <tr><th>Doc ID</th><th>Kind</th><th>PDF</th><th>Source URL</th></tr>
    </thead>
    <tbody>
      {% for d in docs %}
        <tr>
          <td><a href="/doc/{{ d.doc_id }}">{{ d.doc_id }}</a></td>
          <td>{{ d.kind or "" }}</td>
          <td>
            {% if d.pdf %}
              <code title="{{ d.pdf_full or d.pdf }}">{{ d.pdf }}</code>
            {% endif %}
          </td>
          <td>{% if d.source_url %}<code>{{ d.source_url }}</code>{% endif %}</td>
        </tr>
      {% endfor %}
    </tbody>
  </table>
{% endif %}
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
<nav>
  <a href="/">Search</a>
  <a href="/cases">Cases</a>
  {% if case_name %}
    <a href="/case/{{ case_name }}">Back to case</a>
  {% endif %}
  <a href="/web-ingest">Web Ingest</a>
</nav>

<h1>Document: {{ doc_id }}</h1>
<p class="meta">
  {% if case_name %}case={{ case_name }} — {% endif %}
  {% if kind %}kind={{ kind }} — {% endif %}
  {% if pdf %}
    PDF: <code title="{{ pdf_full or pdf }}">{{ pdf }}</code>
    —
  {% endif %}
  {% if source_url %}Source URL: <code>{{ source_url }}</code>{% endif %}
</p>

<h2>OCR Text</h2>
{% if error %}
  <p class="meta">Error reading OCR text: {{ error }}</p>
{% else %}
  <pre>{{ content }}</pre>
{% endif %}
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
<nav>
  <a href="/">Search</a>
  <a href="/cases">Cases</a>
  <a href="/web-ingest">Web Ingest</a>
</nav>

<h1>Web Ingest</h1>
<p class="meta">
  Search the public web for documents (PDF, HTML, text, DOCX, CSV) and ingest them into a case.
  PDFs go through the existing pipeline; other types are converted to text and stored.
</p>

<form method="post" action="/web-ingest">
  <div class="form-row">
    <label for="q">Query</label>
    <input type="text" id="q" name="q" value="{{ query|e }}" placeholder="e.g. Jamal Khashoggi CIA report">
  </div>

  <div class="form-row">
    <label for="case">Case (optional)</label>
    <input type="text" id="case" name="case" value="{{ case|e }}" placeholder="auto if blank">
  </div>

  <div class="form-row">
    <label for="limit">Limit</label>
    <input type="number" id="limit" name="limit" value="{{ limit }}">
  </div>

  <div class="form-row">
    <label for="urls">Manual URLs</label>
    <textarea id="urls" name="urls" rows="4"
      placeholder="One URL per line (optional).&#10;Use this if search returns nothing or you already have the exact links."></textarea>
  </div>

  <p class="meta">
    If the search engine blocks or rate-limits server-side queries (common for data-center IPs),
    paste the document URLs directly in the field above.
  </p>

  <div class="form-row">
    <button type="submit">Search &amp; Ingest</button>
  </div>

  {% if error %}
    <div class="error">{{ error }}</div>
  {% endif %}
</form>

{% if pdf_urls %}
  <h2>URLs Found from Search</h2>
  <ul>
    {% for u in pdf_urls %}
      <li><code>{{ u }}</code></li>
    {% endfor %}
  </ul>
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
          <div>PDF: <code>{{ item.pdf_path }}</code></div>
        {% endif %}
        {% if item.txt_path %}
          <div>TXT: <code>{{ item.txt_path }}</code></div>
        {% endif %}
        {% if not item.pdf_path and not item.txt_path %}
          <div class="meta">No paths recorded (unexpected).</div>
        {% endif %}
      {% endif %}
    </div>
  {% endfor %}
{% endif %}

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
    source_full: str
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

        # Short + full view for local paths
        if isinstance(raw_source, str) and raw_source.startswith("/"):
            source_full = raw_source
            source = f"local:{Path(raw_source).name}"
        else:
            source_full = str(raw_source)
            source = str(raw_source)

        score = float(len(matched_tokens) + total_hits / 10.0)

        p = Path(txt_path)
        results.append(
            SimpleResult(
                doc_id=p.stem,
                source=source,
                source_full=source_full,
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

        # Pretty PDF display (filename only) + full path for potential future tooltips
        pdf_display = None
        pdf_full = None
        if isinstance(pdf, str) and pdf:
            pdf_full = pdf
            if pdf.startswith("/"):
                pdf_display = Path(pdf).name
            else:
                pdf_display = pdf

        p = Path(txt)
        docs.append(
            {
                "doc_id": p.stem,
                "kind": rec.get("kind"),
                "pdf": pdf_display,
                "pdf_full": pdf_full,
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

    # Handle PDF display + full path
    raw_pdf = rec.get("pdf")
    pdf_display = None
    pdf_full = None
    if isinstance(raw_pdf, str) and raw_pdf:
        pdf_full = raw_pdf
        if raw_pdf.startswith("/"):
            pdf_display = Path(raw_pdf).name
        else:
            pdf_display = raw_pdf

    return render_template_string(
        DOC_TEMPLATE,
        style=BASE_STYLE,
        doc_id=doc_id,
        case_name=rec.get("case"),
        kind=rec.get("kind"),
        pdf=pdf_display,
        pdf_full=pdf_full,
        source_url=rec.get("source_url"),
        content=content,
        error=error,
    )


@app.route("/web-ingest", methods=["GET", "POST"])
def web_ingest():
    """
    Web-ingest is available to *any* nginx-authenticated user.
    Nginx basic auth is the real gate; Flask does not ask for another login.

    Two modes:
      - Search-based: use DuckDuckGo HTML to discover doc-like URLs.
      - Manual: user pastes one or more URLs (one per line).
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
        manual_raw = (request.form.get("urls") or "").strip()

        # Parse limit
        try:
            limit = int(limit_raw) if limit_raw else 5
        except ValueError:
            error = "Limit must be an integer."
            limit = 5

        # Normalize / auto-generate case
        if not case:
            # Use query slug if we have a query; otherwise a generic "web" case
            base = query or "web"
            slug = re.sub(r"[^a-z0-9]+", "_", base.lower()).strip("_")
            case = f"{slug}_web" if slug else "web"

        # Manual URLs: one per line, ignore blank lines / non-http
        manual_urls: list[str] = []
        if manual_raw:
            for line in manual_raw.splitlines():
                u = line.strip()
                if not u:
                    continue
                if not u.startswith("http"):
                    # Be strict here; we can relax later if needed
                    continue
                manual_urls.append(u)

        # If we got manual URLs, we don't *need* to hit DuckDuckGo
        urls_to_ingest: list[str] = []

        if manual_urls:
            urls_to_ingest.extend(manual_urls)
        elif not error:
            # Only run search if there was no limit error and no manual URLs
            if not query:
                error = "Query is required if you are not providing manual URLs."
            else:
                try:
                    pdf_urls = fetch_doc_urls(query, limit=limit)
                except Exception as e:
                    error = f"Web search failed: {e}"

                if not error and not pdf_urls:
                    error = (
                        "No document-like URLs found in search results. "
                        "The search engine may be limiting server-side access from this server. "
                        "Try a different query, or paste the URLs directly in the Manual URLs box."
                    )

                urls_to_ingest.extend(pdf_urls)

        # Actually ingest
        if not error and urls_to_ingest:
            for url in urls_to_ingest:
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
