#!/usr/bin/env python
from __future__ import annotations
from flask import current_app
from datetime import datetime
from collections import defaultdict
from pathlib import Path
from urllib.parse import urlparse
import sys
import re
import io
import csv
import os
import json
import time

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

# Ensure project root is on sys.path BEFORE importing vault_core
ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from vault_core.search_backend import run_search, index_txt_document  # type: ignore[import]
from vault_core.manifest import iter_manifest, append_manifest_entry  # type: ignore[import]
from vault_core.ingest.pipeline import ingest_source  # type: ignore[import]
from vault_core.paths import DATA_DIR, OCR_DIR  # type: ignore[import]
from vault_core.search_providers import (
    metasearch,
    MetasearchError,  # uses Brave / Google CSE / Serp
)  # type: ignore[import]

import os
import logging

# DEBUG: show what env key Gunicorn actually sees
logging.warning(
    "DEBUG: Boot env SERPAPI_API_KEY startswith=%r",
    os.getenv("SERPAPI_API_KEY", "")[:8],
)

# -------------------------------
# Flask app setup
# -------------------------------

app = Flask(__name__)
app.secret_key = os.getenv("STRAIGHTLINE_SECRET_KEY", "dev-not-secret")

# ---------- Job queue paths ----------
JOBS_ROOT = Path(os.getenv("STRAIGHTLINE_JOBS_DIR", "/opt/straightline-vault/jobs"))
JOBS_QUEUE_DIR = JOBS_ROOT / "queue"


# ---------- Debug helper ----------

def _log_debug(msg: str) -> None:
    """Simple stderr logger so messages show up in journalctl."""
    print(f"WEBDEBUG: {msg}", file=sys.stderr, flush=True)


# ---------- Job queue writer ----------

def enqueue_ingest_job(url: str, case: str | None) -> Path:
    """
    Write a small JSON job file into jobs/queue for a background worker.
    """
    JOBS_QUEUE_DIR.mkdir(parents=True, exist_ok=True)
    ts = int(time.time() * 1000)
    pid = os.getpid()
    safe_case = re.sub(r"[^a-z0-9]+", "_", (case or "web").lower()).strip("_")
    job_name = f"{ts}-{pid}-{safe_case}.json"
    job_path = JOBS_QUEUE_DIR / job_name

    payload = {"url": url, "case": case}
    job_path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")

    _log_debug(f"enqueue_ingest_job: queued {url!r} case={case!r} -> {job_path}")
    return job_path

# ---------- Web search helpers (metasearch + Brave fallback) ----------

from vault_core.search_providers import (
    metasearch,
    MetasearchError,
    _serpapi_search,  # make sure this import exists at the top
)

def fetch_doc_urls(query: str, limit: int = 10) -> list[str]:
    """
    Fetch document URLs for a query.

    First try SerpAPI directly (we know this works),
    then optionally fall back to metasearch if needed.
    """
    urls: list[str] = []
    limit = max(1, min(limit, 20))

    # 1) Try SerpAPI directly
    current_app.logger.warning(
        "WEBDEBUG: fetch_doc_urls (serpapi-first): query=%r, limit=%r", query, limit
    )
    try:
        serp_results = _serpapi_search(query, max_results=limit)
        current_app.logger.warning(
            "WEBDEBUG: fetch_doc_urls: _serpapi_search returned %d results",
            len(serp_results),
        )
        for r in serp_results:
            # r is a SearchResult dataclass: has .url, .title, .snippet, .provider
            url = getattr(r, "url", None)
            if url and url not in urls:
                urls.append(url)
        if urls:
            return urls[:limit]
    except Exception as e:
        current_app.logger.warning(
            "WEBDEBUG: fetch_doc_urls: direct SerpAPI failed: %r", e
        )

    # 2) Optional fallback to metasearch (if you want to keep it around)
    try:
        current_app.logger.warning(
            "WEBDEBUG: fetch_doc_urls: falling back to metasearch(max_results=%d)",
            limit,
        )
        results = metasearch(query, max_results=limit)
        for r in results:
            url = getattr(r, "url", None)
            if url and url not in urls:
                urls.append(url)
    except MetasearchError as e:
        current_app.logger.warning(
            "WEBDEBUG: fetch_doc_urls: metasearch_error after SerpAPI: %r", e
        )

    return urls[:limit]

# ---------- URL ingest helpers ----------

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
    Returns the txt_path. Indexing is handled by the caller.
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
    """
    Flatten CSV text into a readable, line-based text block for indexing.
    """
    out_lines: list[str] = []
    reader = csv.reader(text.splitlines())
    for row in reader:
        out_lines.append("\t".join(cell.strip() for cell in row))
    return "\n".join(out_lines)


# ---------- Non-hanging web ingest (used by background worker) ----------

def ingest_url_web(url: str, case: str | None):
    """
    Ingest a URL for the web UI / background worker with short, safe networking:

      - SINGLE GET request (no HEAD)
      - Short timeout so multiple URLs don't hang the worker
      - Same behaviors for PDF/HTML/text/CSV/DOCX

    Returns (pdf_path, txt_path) where pdf_path may be None.
    Raises ValueError on unsupported types.
    """

    _log_debug(f"ingest_url_web: START url={url!r}, case={case!r}")

    headers = {
        "User-Agent": (
            "Mozilla/5.0 (StraightlineVault/0.1; +non-malicious investigative use)"
        ),
        "Accept": "*/*",
    }

    # Single GET with short timeout
    try:
        resp = requests.get(
            url,
            headers=headers,
            timeout=(5, 10),  # (connect_timeout, read_timeout)
            allow_redirects=True,
        )
        resp.raise_for_status()
        ctype = (resp.headers.get("Content-Type") or "").lower()
        _log_debug(
            f"ingest_url_web: GET ok url={url}, status={resp.status_code}, ctype={ctype}"
        )
    except Exception as e:
        _log_debug(f"ingest_url_web: GET FAILED url={url}: {e!r}")
        raise RuntimeError(f"Network error fetching {url}: {e}")

    # PDF: existing pipeline does everything (download, OCR, index)
    if "pdf" in ctype or url.lower().endswith(".pdf"):
        try:
            _log_debug(
                f"ingest_url_web: PDF detected, delegating to ingest_source: {url}"
            )
            pdf_path, txt_path = ingest_source(url, case=case)
            return pdf_path, txt_path
        except Exception as e:
            _log_debug(f"ingest_url_web: ingest_source FAILED for {url}: {e!r}")
            raise

    # DOCX
    if (
        "officedocument.wordprocessingml.document" in ctype
        or url.lower().endswith(".docx")
    ):
        try:
            _log_debug("ingest_url_web: DOCX detected")
            text = _extract_docx_text(resp.content)
            txt_path = _write_txt_and_manifest(text, url, case, kind="web_docx")
            try:
                index_txt_document(str(txt_path))
            except Exception as e:
                _log_debug(
                    f"ingest_url_web: DOCX index_txt_document FAILED for {txt_path}: {e!r}"
                )
            return None, txt_path
        except Exception as e:
            _log_debug(f"ingest_url_web: DOCX extract FAILED for {url}: {e!r}")
            raise

    # CSV
    if "text/csv" in ctype or url.lower().endswith(".csv"):
        try:
            _log_debug("ingest_url_web: CSV detected")
            raw_text = resp.text
            text = _extract_csv_text(raw_text)
            txt_path = _write_txt_and_manifest(text, url, case, kind="web_csv")
            try:
                index_txt_document(str(txt_path))
            except Exception as e:
                _log_debug(
                    f"ingest_url_web: CSV index_txt_document FAILED for {txt_path}: {e!r}"
                )
            return None, txt_path
        except Exception as e:
            _log_debug(f"ingest_url_web: CSV extract FAILED for {url}: {e!r}")
            raise

    # HTML
    if "html" in ctype or url.lower().endswith((".htm", ".html", "/")):
        try:
            _log_debug("ingest_url_web: HTML detected")
            soup = BeautifulSoup(resp.text, "html.parser")
            text = soup.get_text("\n", strip=True)
            txt_path = _write_txt_and_manifest(text, url, case, kind="web_html")
            try:
                index_txt_document(str(txt_path))
            except Exception as e:
                _log_debug(
                    f"ingest_url_web: HTML index_txt_document FAILED for {txt_path}: {e!r}"
                )
            return None, txt_path
        except Exception as e:
            _log_debug(f"ingest_url_web: HTML parse FAILED for {url}: {e!r}")
            raise

    # Generic text/*
    if ctype.startswith("text/"):
        try:
            _log_debug("ingest_url_web: text/* detected")
            text = resp.text
            txt_path = _write_txt_and_manifest(text, url, case, kind="web_text")
            try:
                index_txt_document(str(txt_path))
            except Exception as e:
                _log_debug(
                    f"ingest_url_web: TEXT index_txt_document FAILED for {txt_path}: {e!r}"
                )
            return None, txt_path
        except Exception as e:
            _log_debug(f"ingest_url_web: TEXT ingest FAILED for {url}: {e!r}")
            raise

    # Fallback
    _log_debug(f"ingest_url_web: UNSUPPORTED CONTENT TYPE {ctype!r}")
    raise ValueError(f"Unsupported content type for ingest: {ctype or 'unknown'}")


# ---------- Base Styles ----------

BASE_STYLE = """
<style>
  :root {
    color-scheme: dark;
  }

  body {
    background: #05070b;
    color: #e7ecf5;
    font-family: system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
    margin: 2rem;
    max-width: 960px;
  }

  a {
    color: #7fb4ff;
    text-decoration: none;
  }
  a:hover {
    text-decoration: underline;
  }

  nav {
    margin-bottom: 1.5rem;
  }
  nav a {
    margin-right: 1rem;
    font-size: 0.95rem;
  }

  h1 {
    margin-bottom: 0.25rem;
  }
  h2 {
    margin-top: 1.5rem;
  }

  form {
    margin-bottom: 1.5rem;
  }
  label {
    display: inline-block;
    min-width: 4.5rem;
  }
  input[type="text"],
  input[type="number"] {
    width: 25rem;
    background: #111827;
    border: 1px solid #374151;
    color: #e7ecf5;
    padding: 0.35rem 0.5rem;
    border-radius: 4px;
  }

  button {
    background: #2563eb;
    border: none;
    color: #f9fafb;
    padding: 0.35rem 0.9rem;
    border-radius: 4px;
    cursor: pointer;
  }
  button:hover {
    background: #1d4ed8;
  }

  .meta {
    color: #9ca3af;
    font-size: 0.9rem;
  }

  .result {
    border-bottom: 1px solid #1f2937;
    padding: 0.75rem 0;
  }

  .snippet {
    margin-top: 0.5rem;
  }

  .score {
    font-size: 0.85rem;
    color: #9ca3af;
  }

  code, pre {
    font-family: ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas,
      "Liberation Mono", "Courier New", monospace;
    font-size: 0.9rem;
  }

  table {
    border-collapse: collapse;
    width: 100%;
  }
  th, td {
    border-bottom: 1px solid #1f2937;
    padding: 0.4rem 0.25rem;
    text-align: left;
  }
  th {
    font-weight: 600;
  }

  .error {
    color: #f97373;
    margin-top: 0.5rem;
  }

  .ingested-item {
    border-bottom: 1px solid #1f2937;
    padding: 0.5rem 0;
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
<nav>
  <a href="/">Search</a>
  <a href="/cases">Cases</a>
  <a href="/recent">Recent</a>
  <a href="/web-ingest">Web Ingest</a>
</nav>

<h1>Straightline Vault</h1>
<p class="meta">Full-text search across your ingested corpus. Nginx basic auth protects this interface.</p>

<form method="get" action="/">
  <div>
    <label for="q">Query</label>
    <input type="text" id="q" name="q" value="{{ q|e }}" autofocus>
  </div>
  <div>
    <label for="case">Case</label>
    <input type="text" id="case" name="case" value="{{ case|e }}" placeholder="optional">
  </div>
  <div>
    <label for="kind">Kind</label>
    <input type="text" id="kind" name="kind" value="{{ kind|e }}" placeholder="local_file, url_fetch, web_html">
  </div>
  <div style="margin-top: 0.5rem;">
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
          Source: <code>{{ r.source }}</code>
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
  <a href="/recent">Recent</a>
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
  <a href="/recent">Recent</a>
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
          <td><code>{{ d.pdf or "" }}</code></td>
          <td>{% if d.source_url %}<code>{{ d.source_url }}</code>{% endif %}</td>
        </tr>
      {% endfor %}
    </tbody>
  </table>
{% endif %}
</body></html>
"""

RECENT_TEMPLATE = """
<!doctype html>
<html><head>
<meta charset="utf-8"><title>Straightline Vault — Recent Docs</title>
{{ style|safe }}
</head>
<body>
<nav>
  <a href="/">Search</a>
  <a href="/cases">Cases</a>
  <a href="/recent">Recent</a>
  <a href="/web-ingest">Web Ingest</a>
</nav>

<h1>Recent Documents</h1>
<p class="meta">
  Most recent ingested documents, ordered by manifest timestamp (newest first).
</p>

{% if not docs %}
  <p>No documents found in manifest.</p>
{% else %}
  <table>
    <thead>
      <tr>
        <th>Timestamp (UTC)</th>
        <th>Doc ID</th>
        <th>Case</th>
        <th>Kind</th>
        <th>Source URL</th>
      </tr>
    </thead>
    <tbody>
      {% for d in docs %}
        <tr>
          <td><code>{{ d.timestamp }}</code></td>
          <td><a href="/doc/{{ d.doc_id }}">{{ d.doc_id }}</a></td>
          <td>{{ d.case or "" }}</td>
          <td>{{ d.kind or "" }}</td>
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
  <a href="/recent">Recent</a>
  <a href="/web-ingest">Web Ingest</a>
</nav>

<h1>Document: {{ doc_id }}</h1>
<p class="meta">
  {% if case_name %}case={{ case_name }} — {% endif %}
  {% if kind %}kind={{ kind }} — {% endif %}
  {% if pdf %}PDF: <code>{{ pdf }}</code> — {% endif %}
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
  <a href="/recent">Recent</a>
  <a href="/web-ingest">Web Ingest</a>
</nav>

<h1>Web Ingest</h1>
<p class="meta">
  Search the public web for documents (PDF, HTML, text, DOCX, CSV) and ingest them into a case.
  PDFs go through the existing pipeline; other types are converted to text and stored.
</p>

<form method="post" action="/web-ingest">
  <div>
    <label for="q">Query</label>
    <input type="text" id="q" name="q" value="{{ query|e }}" placeholder="e.g. Jamal Khashoggi CIA report">
  </div>

  <div>
    <label for="case">Case (optional)</label>
    <input type="text" id="case" name="case" value="{{ case|e }}" placeholder="auto if blank">
  </div>

  <div>
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
        {% if item.job_path %}
          <div>Job file: <code>{{ item.job_path }}</code></div>
        {% else %}
          <div class="meta">Queued (no job_path recorded).</div>
        {% endif %}
      {% endif %}
    </div>
  {% endfor %}
{% endif %}

{% if queue_jobs %}
  <h2>Pending Jobs</h2>
  <p class="meta">{{ queue_jobs|length }} job(s) currently in queue.</p>
  <table>
    <thead>
      <tr><th>Job file</th><th>Case</th><th>URL</th><th>Queued at (UTC)</th></tr>
    </thead>
    <tbody>
      {% for j in queue_jobs %}
        <tr>
          <td><code>{{ j.name }}</code></td>
          <td>{{ j.case or "" }}</td>
          <td><code>{{ j.url or "" }}</code></td>
          <td>{{ j.queued_at }}</td>
        </tr>
      {% endfor %}
    </tbody>
  </table>
{% endif %}

</body></html>
"""


# ---------- Helpers ----------

def build_case_stats():
    stats = defaultdict(lambda: {"total": 0, "kinds": defaultdict(int)})
    for rec in iter_manifest() or []:
        case = rec.get("case") or "uncategorized"
        kind = rec.get("kind") or "unknown"
        stats[case]["total"] += 1
        stats[case]["kinds"][kind] += 1
    return sorted(
        (
            (
                name,
                {
                    "total": info["total"],
                    "kinds": dict(info["kinds"]),
                },
            )
            for name, info in stats.items()
        ),
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
    try:
        p = Path(path_str)
        if not p.is_absolute():
            # manifest txt paths are stored relative to DATA_DIR
            p = DATA_DIR / p
        text = p.read_text(encoding="utf-8", errors="replace")
        return text, None
    except Exception as e:
        return None, str(e)


def build_manifest_index():
    """
    Build a mapping of doc_id -> manifest record.
    doc_id is derived from the stem of the TXT path.
    """
    index: dict[str, dict] = {}
    for rec in iter_manifest() or []:
        txt = rec.get("txt")
        if not txt:
            continue
        p = Path(txt)
        doc_id = p.stem
        index[doc_id] = rec
    return index


def iter_recent_docs(limit: int = 50):
    """
    Return a list of the most recent manifest entries, newest first.

    Each item:
      {
        "doc_id": str,
        "timestamp": str,
        "case": Optional[str],
        "kind": Optional[str],
        "source_url": Optional[str],
      }
    """
    records = list(iter_manifest() or [])

    def _parse_ts(rec):
        ts = rec.get("timestamp")
        if not ts:
            return datetime.min
        try:
            # Handles normal isoformat, including fractions
            return datetime.fromisoformat(ts)
        except Exception:
            return datetime.min

    # Sort newest first
    records.sort(key=_parse_ts, reverse=True)

    docs = []
    for rec in records[:limit]:
        txt = rec.get("txt")
        if not txt:
            continue
        p = Path(txt)
        doc_id = p.stem

        docs.append(
            {
                "doc_id": doc_id,
                "timestamp": rec.get("timestamp") or "",
                "case": rec.get("case"),
                "kind": rec.get("kind"),
                "source_url": rec.get("source_url"),
            }
        )

    return docs


# ---------- Routes ----------

@app.route("/", methods=["GET"])
def index():
    q = request.args.get("q", "").strip()
    case = request.args.get("case", "").strip() or None
    kind = request.args.get("kind", "").strip() or None
    limit_str = request.args.get("limit", "") or "20"

    try:
        limit = int(limit_str)
    except ValueError:
        limit = 20

    raw_results = run_search(q, case=case, kind=kind, limit=limit) if q else []

    # Build an index of doc_id -> manifest record so we can attach metadata
    manifest_index = build_manifest_index()

    enriched_results = []
    for r in raw_results:
        # Support both attribute-style and dict-style results, just in case
        doc_id = getattr(r, "doc_id", None) or getattr(r, "id", None)
        if doc_id is None and isinstance(r, dict):
            doc_id = r.get("doc_id") or r.get("id")

        score = getattr(r, "score", None)
        snippet = getattr(r, "snippet", None)
        source = getattr(r, "source", None)

        if isinstance(r, dict):
            score = score if score is not None else r.get("score")
            snippet = snippet if snippet is not None else r.get("snippet")
            source = source if source is not None else r.get("source")

        rec = manifest_index.get(doc_id, {}) if doc_id else {}

        enriched_results.append(
            {
                "doc_id": doc_id,
                "score": score,
                "snippet": snippet,
                # Prefer search backend's source, fall back to manifest source_url/txt/pdf
                "source": source
                or rec.get("source_url")
                or rec.get("txt")
                or rec.get("pdf"),
                "case": rec.get("case"),
                "kind": rec.get("kind"),
            }
        )

    return render_template_string(
        INDEX_TEMPLATE,
        style=BASE_STYLE,
        q=q,
        case=case or "",
        kind=kind or "",
        limit=limit,
        results=enriched_results,
    )


@app.route("/cases", methods=["GET"])
def cases_view():
    return render_template_string(
        CASES_TEMPLATE,
        style=BASE_STYLE,
        cases=build_case_stats(),
    )


@app.route("/recent", methods=["GET"])
def recent_view():
    docs = iter_recent_docs(limit=50)
    return render_template_string(
        RECENT_TEMPLATE,
        style=BASE_STYLE,
        docs=docs,
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
    limit = 10
    error: str | None = None
    pdf_urls: list[str] = []
    ingested: list[dict] = []

    if request.method == "POST":
        query = (request.form.get("q") or "").strip()
        case = (request.form.get("case") or "").strip()
        limit_raw = (request.form.get("limit") or "").strip()

        # parse + clamp limit so a typo doesn't wreck the worker
        try:
            limit = int(limit_raw) if limit_raw else 10
        except ValueError:
            error = "Limit must be an integer."
            limit = 10

        if limit < 1:
            limit = 1
        if limit > 20:
            limit = 20

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
                            job_path = enqueue_ingest_job(url, case=case or None)
                            ingested.append(
                                {
                                    "url": url,
                                    "job_path": str(job_path),
                                    "error": None,
                                }
                            )
                        except Exception as e:
                            ingested.append(
                                {
                                    "url": url,
                                    "job_path": None,
                                    "error": str(e),
                                }
                            )

    # --- Always show current queue state, even on GET ---

    queue_jobs: list[dict] = []
    try:
        if JOBS_QUEUE_DIR.exists():
            for p in sorted(JOBS_QUEUE_DIR.glob("*.json")):
                # filename pattern: {ts}-{pid}-{safe_case}.json
                name = p.name
                ts_ms = None
                case_from_name: str | None = None

                parts = name.split("-", 2)
                if len(parts) >= 2:
                    # first part is timestamp in ms
                    try:
                        ts_ms = int(parts[0])
                    except ValueError:
                        ts_ms = None
                    if len(parts) == 3:
                        # third part includes safe_case plus ".json"
                        case_from_name = parts[2].rsplit(".", 1)[0]

                queued_at = ""
                if ts_ms is not None:
                    try:
                        queued_at = datetime.utcfromtimestamp(ts_ms / 1000.0).isoformat()
                    except Exception:
                        queued_at = ""

                # also try to read payload for url/case
                url = ""
                case_field: str | None = None
                try:
                    payload = json.loads(p.read_text(encoding="utf-8"))
                    url = str(payload.get("url") or "")
                    case_field = payload.get("case")
                except Exception:
                    pass

                queue_jobs.append(
                    {
                        "name": name,
                        "url": url,
                        "case": case_field or case_from_name,
                        "queued_at": queued_at,
                    }
                )
    except Exception as e:
        _log_debug(f"web_ingest: failed to list queue jobs: {e!r}")
        queue_jobs = []

    return render_template_string(
        WEB_INGEST_TEMPLATE,
        style=BASE_STYLE,
        query=query,
        case=case,
        limit=limit,
        error=error,
        pdf_urls=pdf_urls,
        ingested=ingested,
        queue_jobs=queue_jobs,
    )
