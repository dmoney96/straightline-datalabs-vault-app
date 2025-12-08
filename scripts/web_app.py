#!/usr/bin/env python

from __future__ import annotations

# --- Standard library imports ---
import os
import sys
import re
import io
import csv
import json
import time
import logging
from datetime import datetime, timezone
from pathlib import Path
from collections import defaultdict
from urllib.parse import urlparse

# --- Third-party imports ---
import requests
from bs4 import BeautifulSoup
from flask import (
    Flask,
    request,
    render_template,
    abort,
    current_app,
)

# Optional DOCX parsing
try:
    from docx import Document  # type: ignore[import]
except ImportError:
    Document = None

# --- Ensure project root is importable ---
ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

# --- Flask app setup (single app instance) ---
app = Flask(__name__, template_folder=str(ROOT / "templates"))
app.secret_key = os.getenv("STRAIGHTLINE_SECRET_KEY", "dev-not-secret")

# --- Internal Straightline Vault imports ---

# External web search providers
from vault_core.search_providers import (
    metasearch,
    MetasearchError,
    _serpapi_search,
    _brave_search,
)

# Manifest helpers
from vault_core.manifest import (
    DATA_DIR,
    iter_manifest,
    append_manifest_entry,
)

# Search backend (Whoosh index)
from vault_core.search.indexer import (
    run_search,
    update_index_for_file,
)

# Fallback OCR directory (manifest doesn't define OCR_DIR constant)
OCR_DIR = DATA_DIR / "ocr"


def index_txt_document(txt_path: str | Path) -> None:
    """
    Minimal wrapper around the Whoosh indexer so web_app doesn't
    assume anything about higher-level ingest code.
    """
    update_index_for_file(Path(txt_path))


def ingest_source(url: str, case: str | None = None):
    """
    Placeholder hook for PDF ingest.

    This function is called by ingest_url_web() for PDF URLs.
    It is intentionally *not* implemented here so we don't guess
    about your existing ingest pipeline layout.

    Wire this up to your real ingest function (wherever it lives)
    or replace this stub with an implementation that:
      - downloads the PDF,
      - stores it under your data tree,
      - OCRs it into TXT,
      - appends a manifest entry,
      - updates the index.

    Expected return shape:
        (pdf_path: Path | None, txt_path: Path | None)
    """
    raise NotImplementedError(
        "ingest_source(url, case) is not implemented in scripts/web_app.py. "
        "Wire this to your existing ingest pipeline."
    )


# --- Debug: confirm search API keys at boot ---
logging.warning(
    "DEBUG: Boot env SERPAPI_API_KEY=%r BRAVE_API_KEY=%r BRAVE_SUBSCRIPTION_TOKEN=%r",
    os.getenv("SERPAPI_API_KEY", "")[:8],
    os.getenv("BRAVE_API_KEY", "")[:8],
    os.getenv("BRAVE_SUBSCRIPTION_TOKEN", "")[:8],
)

# -------------------------------
# Job queue paths
# -------------------------------

JOBS_ROOT = Path(os.getenv("STRAIGHTLINE_JOBS_DIR", "/opt/straightline-vault/jobs"))
JOBS_QUEUE_DIR = JOBS_ROOT / "queue"


# -------------------------------
# Debug helper
# -------------------------------

def _log_debug(msg: str) -> None:
    """Simple stderr logger so messages show up in journalctl."""
    print(f"WEBDEBUG: {msg}", file=sys.stderr, flush=True)


# -------------------------------
# Job queue writer
# -------------------------------

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


# -------------------------------
# Web search helpers (Brave → SerpAPI → metasearch)
# -------------------------------

def fetch_doc_urls(query: str, limit: int = 10) -> list[str]:
    """
    Fetch document URLs for a query.

    Priority:
      1) Brave Web Search (BRAVE_API_KEY / BRAVE_SUBSCRIPTION_TOKEN)
      2) SerpAPI (SERPAPI_API_KEY)
      3) metasearch() as a last-ditch fallback

    Returns a list of unique URLs, up to `limit`.
    """
    urls: list[str] = []
    limit = max(1, min(limit, 20))

    log = current_app.logger
    log.warning("WEBDEBUG: fetch_doc_urls: query=%r, limit=%r", query, limit)

    # --- 1) Brave first ---
    try:
        log.warning("WEBDEBUG: fetch_doc_urls: trying Brave first")
        brave_results = _brave_search(query, max_results=limit)
        log.warning(
            "WEBDEBUG: fetch_doc_urls: Brave returned %d result(s)",
            len(brave_results),
        )
        for r in brave_results:
            url = getattr(r, "url", None)
            if url and url not in urls:
                urls.append(url)
        if urls:
            return urls[:limit]
    except MetasearchError as e:
        log.warning("WEBDEBUG: Brave search unavailable: %r", e)
    except Exception as e:
        log.warning("WEBDEBUG: Brave search error: %r", e)

    # --- 2) SerpAPI direct ---
    try:
        log.warning("WEBDEBUG: fetch_doc_urls: falling back to SerpAPI")
        serp_results = _serpapi_search(query, max_results=limit)
        log.warning(
            "WEBDEBUG: fetch_doc_urls: SerpAPI returned %d result(s)",
            len(serp_results),
        )
        for r in serp_results:
            url = getattr(r, "url", None)
            if url and url not in urls:
                urls.append(url)
        if urls:
            return urls[:limit]
    except MetasearchError as e:
        log.warning("WEBDEBUG: SerpAPI unavailable: %r", e)
    except Exception as e:
        log.warning("WEBDEBUG: fetch_doc_urls: SerpAPI error: %r", e)

    # --- 3) metasearch() fallback ---
    try:
        log.warning("WEBDEBUG: fetch_doc_urls: falling back to metasearch()")
        results = metasearch(query, max_results=limit)
        for r in results:
            url = getattr(r, "url", None)
            if url and url not in urls:
                urls.append(url)
    except MetasearchError as e:
        log.warning(
            "WEBDEBUG: metasearch unavailable after Brave+SerpAPI: %r", e
        )
    except Exception as e:
        log.warning("WEBDEBUG: fetch_doc_urls: metasearch error: %r", e)

    return urls[:limit]


# -------------------------------
# URL ingest helpers
# -------------------------------

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


# -------------------------------
# Non-hanging web ingest (used by background worker)
# -------------------------------

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

    # PDF: delegate to your main ingest pipeline (stub here)
    if "pdf" in ctype or url.lower().endswith(".pdf"):
        _log_debug(
            f"ingest_url_web: PDF detected, delegating to ingest_source: {url}"
        )
        pdf_path, txt_path = ingest_source(url, case=case)
        return pdf_path, txt_path

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


# -------------------------------
# Helper functions
# -------------------------------

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
            # Normalize "Z" to explicit UTC offset, if present
            ts_norm = ts.replace("Z", "+00:00")
            dt = datetime.fromisoformat(ts_norm)

            # Force everything to *naive UTC* so comparisons are valid
            if dt.tzinfo is not None:
                dt = dt.astimezone(timezone.utc).replace(tzinfo=None)
            return dt
        except Exception:
            # If anything goes weird, treat as oldest
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


# -------------------------------
# Routes
# -------------------------------

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
                # Prefer search backend's source, fall back to manifest
                "source": (
                    source
                    or rec.get("source_url")
                    or rec.get("txt")
                    or rec.get("pdf")
                ),
                "case": rec.get("case"),
                "kind": rec.get("kind"),
            }
        )

    return render_template(
        "search.html",
        q=q,
        case=case or "",
        kind=kind or "",
        limit=limit,
        results=enriched_results,
        error=None,
        active_nav="search",
    )


@app.route("/web-ingest", methods=["GET", "POST"])
def web_ingest():
    """
    Web-ingest is available to *any* nginx-authenticated user.
    Nginx basic auth is the real gate; Flask does not ask for another login.
    """
    query: str = ""
    case: str = ""
    limit: int = 10
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
                ts_ms: int | None = None
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
                        queued_at = datetime.utcfromtimestamp(
                            ts_ms / 1000.0
                        ).isoformat()
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

    return render_template(
        "web_ingest.html",
        query=query,
        case=case,
        limit=limit,
        error=error,
        pdf_urls=pdf_urls,
        ingested=ingested,
        queue_jobs=queue_jobs,
        active_nav="web_ingest",
    )


@app.route("/cases", methods=["GET"])
def cases_view():
    """
    Cases / corpora overview.
    Adapt build_case_stats() output into the shape expected by cases.html:
      - name
      - doc_count
      - kinds (list or string)
    """
    raw_stats = build_case_stats()
    cases = []

    for case_name, info in raw_stats:
        # info: {"total": int, "kinds": {kind: count}}
        kinds_dict = info.get("kinds", {}) or {}

        # Turn kind counts into a readable list like ["local_file=10", "web_html=3"]
        kinds_list = [f"{k}={v}" for k, v in kinds_dict.items()]

        cases.append(
            {
                "name": case_name,
                "doc_count": info.get("total", 0),
                "kinds": kinds_list,
            }
        )

    return render_template(
        "cases.html",
        cases=cases,
        active_nav="cases",
    )

@app.route("/recent", methods=["GET"])
def recent_view():
    docs = iter_recent_docs(limit=50)
    return render_template(
        "recent.html",
        docs=docs,
        active_nav="recent",
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

    return render_template(
        "case.html",
        case_name=case_name,
        docs=docs,
        active_nav="cases",
    )


@app.route("/doc/<doc_id>", methods=["GET"])
def doc_view(doc_id: str):
    rec = find_manifest_by_doc_id(doc_id)
    if not rec:
        abort(404, description=f"No manifest record found for doc_id={doc_id!r}")

    txt_path = rec.get("txt")
    content, error = load_ocr_text(txt_path) if txt_path else (None, "TXT path missing")

    return render_template(
        "doc.html",
        doc_id=doc_id,
        case_name=rec.get("case"),
        kind=rec.get("kind"),
        pdf=rec.get("pdf"),
        source_url=rec.get("source_url"),
        content=content,
        error=error,
        active_nav=None,
    )
