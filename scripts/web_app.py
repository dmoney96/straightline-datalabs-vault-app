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
import subprocess
from datetime import datetime, timezone, timedelta
from pathlib import Path
from collections import defaultdict
from urllib.parse import urlparse
from functools import wraps
import ipaddress
import socket
import hmac

# --- Third-party imports ---
import requests
from requests import HTTPError
from bs4 import BeautifulSoup

from flask import (
    Flask,
    request,
    render_template,
    abort,
    current_app,
    redirect,
    url_for,
    session,
)

# --- Optional DOCX parsing ---
try:
    from docx import Document  # type: ignore[import]
except ImportError:
    Document = None

# -------------------------------------------------------------------
# Path / import setup (MUST happen before app init)
# -------------------------------------------------------------------
ROOT = Path(__file__).resolve().parents[1]  # /home/dom/vault-app
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

SCRIPTS_DIR = ROOT / "scripts"
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

# -------------------------------------------------------------------
# Flask app (single instance)
# -------------------------------------------------------------------
app = Flask(__name__, template_folder=str(ROOT / "templates"))

# Require secret key (don’t silently run insecure in prod)
secret_key = os.getenv("STRAIGHTLINE_SECRET_KEY")
if not secret_key:
    logging.error("STRAIGHTLINE_SECRET_KEY not set in environment!")
    raise RuntimeError("STRAIGHTLINE_SECRET_KEY must be set")
app.secret_key = secret_key

# Session cookie hardening
SESSION_MINUTES = int(os.getenv("STRAIGHTLINE_SESSION_MINUTES", "240"))  # default 4 hours
app.config.update(
    SESSION_COOKIE_HTTPONLY=True,
    SESSION_COOKIE_SECURE=True,  # requires HTTPS (nginx terminates TLS)
    SESSION_COOKIE_SAMESITE="Lax",
    PERMANENT_SESSION_LIFETIME=timedelta(
        minutes=max(5, min(SESSION_MINUTES, 7 * 24 * 60))
    ),
)

# Optional CSRF enforcement (off by default)
ENFORCE_CSRF = os.getenv("STRAIGHTLINE_CSRF", "0") == "1"

# -------------------------------------------------------------------
# Security middleware (optional but should not break app boot)
# -------------------------------------------------------------------
limiter = None
try:
    from security_middleware import init_security  # type: ignore
    limiter = init_security(app)
    logging.warning("Security middleware initialized successfully")
except Exception as e:
    logging.error("Security middleware init failed: %r", e)

# -------------------------------------------------------------------
# Access token (optional; used for internal CLI/API access)
# -------------------------------------------------------------------
ACCESS_TOKEN = os.getenv("STRAIGHTLINE_ACCESS_TOKEN") or ""

# -------------------------------------------------------------------
# Internal Straightline Vault imports
# -------------------------------------------------------------------
from vault_core.search_providers import metasearch  # noqa: E402
from vault_core.manifest import DATA_DIR, iter_manifest, append_manifest_entry  # noqa: E402
from vault_core.search.indexer import run_search, update_index_for_file  # noqa: E402

from headless_fetch import headless_can_use_for, headless_fetch_html, HEADLESS_ON_403  # noqa: E402

# Auth DB helper (SQLite)
from vault_core.auth_db import consume_invite, verify_user  # noqa: E402

# Fallback OCR directory
OCR_DIR = DATA_DIR / "ocr"

# -------------------------------------------------------------------
# Logging helpers
# -------------------------------------------------------------------
def _log_debug(msg: str) -> None:
    # Keep it non-sensitive: never print tokens/passwords/env secrets.
    if os.getenv("STRAIGHTLINE_DEBUG_SEARCH") == "1":
        print(f"WEBDEBUG: {msg}", file=sys.stderr, flush=True)

# -------------------------------------------------------------------
# Security headers (defense-in-depth)
# -------------------------------------------------------------------
@app.after_request
def _add_security_headers(resp):
    resp.headers.setdefault("X-Content-Type-Options", "nosniff")
    resp.headers.setdefault("X-Frame-Options", "DENY")
    resp.headers.setdefault("Referrer-Policy", "no-referrer")
    resp.headers.setdefault(
        "Content-Security-Policy",
        "default-src 'self'; frame-ancestors 'none'; base-uri 'self'",
    )
    return resp

# -------------------------------------------------------------------
# CSRF helper (optional)
# -------------------------------------------------------------------
def _get_csrf_token() -> str:
    tok = session.get("csrf_token")
    if not tok:
        tok = os.urandom(16).hex()
        session["csrf_token"] = tok
    return str(tok)

def _require_csrf() -> None:
    if not ENFORCE_CSRF:
        return
    if request.method != "POST":
        return
    provided = (request.form.get("csrf_token") or request.headers.get("X-CSRF-Token") or "").strip()
    expected = (session.get("csrf_token") or "").strip()
    if not expected or not provided or not hmac.compare_digest(provided, expected):
        abort(400, description="CSRF token missing or invalid")

# -------------------------------------------------------------------
# Auth helpers (Option B)
# -------------------------------------------------------------------
def _is_logged_in() -> bool:
    return bool(session.get("auth") == "1" and session.get("user"))

def _safe_next_url(next_url: str) -> str:
    next_url = (next_url or "").strip()
    if not next_url:
        return url_for("vault_search")
    parsed = urlparse(next_url)
    # No open redirects
    if parsed.scheme or parsed.netloc:
        return url_for("vault_search")
    if not next_url.startswith("/"):
        return url_for("vault_search")
    return next_url

@app.before_request
def _refresh_session_on_activity():
    # Sliding expiration: keep people logged in while actively using the site.
    # If you want absolute expiration only, delete this function.
    if _is_logged_in():
        session.permanent = True
        session.modified = True

def require_access(view):
    @wraps(view)
    def wrapped(*args, **kwargs):
        # 1) Session login (Option B)
        if _is_logged_in():
            return view(*args, **kwargs)

        # 2) API/tooling bypass (optional)
        expected = (ACCESS_TOKEN or "").strip()
        if expected:
            provided = (request.headers.get("X-Access-Token") or "").strip()
            if provided and hmac.compare_digest(provided, expected):
                return view(*args, **kwargs)

        # 3) Not authorized -> login (preserve next)
        next_url = request.full_path if request.query_string else request.path
        return redirect(url_for("login", next=next_url))
    return wrapped

# -------------------------------------------------------------------
# SSRF guard
# -------------------------------------------------------------------
def _guard_remote_url(url: str) -> None:
    parsed = urlparse(url)

    if parsed.scheme not in ("http", "https"):
        raise ValueError(f"unsupported scheme: {parsed.scheme!r}")

    host = parsed.hostname
    if not host:
        raise ValueError("URL missing hostname")

    try:
        infos = socket.getaddrinfo(host, None)
    except Exception as e:
        _log_debug(f"_guard_remote_url: DNS failure for {host!r}: {e!r}")
        raise ValueError("dns failure") from e

    resolved_ips = sorted({info[4][0] for info in infos})
    _log_debug(f"_guard_remote_url: resolves host={host!r} -> {resolved_ips}")

    for ip_str in resolved_ips:
        ip = ipaddress.ip_address(ip_str)
        if (
            ip.is_loopback
            or ip.is_private
            or ip.is_link_local
            or ip.is_multicast
            or ip.is_reserved
        ):
            _log_debug(f"_guard_remote_url: blocked non-public IP {ip_str}")
            raise ValueError("non-public ip blocked")

# -------------------------------------------------------------------
# Search result presentation helpers (YOU WERE MISSING THESE)
# -------------------------------------------------------------------
def _humanize_doc_id(doc_id: str) -> str:
    s = doc_id.replace("_", " ").strip()
    s = re.sub(r"\s+", " ", s)
    return s[:1].upper() + s[1:] if s else doc_id

def _domain_from_url(url: str) -> str:
    try:
        host = (urlparse(url).hostname or "").lower()
        return host or ""
    except Exception:
        return ""

def _summarize_path(path_str: str) -> str:
    try:
        p = Path(path_str)
        parts = p.parts[-2:] if len(p.parts) >= 2 else p.parts
        return "/".join(parts)
    except Exception:
        return path_str

def _clip_snippet(snippet: str, max_chars: int = 420) -> str:
    if not snippet:
        return ""
    if len(snippet) <= max_chars:
        return snippet
    return snippet[: max_chars - 1].rstrip() + "…"

def find_manifest_by_doc_id(doc_id: str):
    for rec in iter_manifest() or []:
        txt = rec.get("txt")
        if not txt:
            continue
        p = Path(str(txt))
        if p.stem == doc_id:
            return rec
    return None

def _decorate_hit(hit: dict) -> dict:
    doc_id = str(hit.get("doc_id") or "")
    source_file = str(hit.get("source_file") or "")
    snippet = str(hit.get("snippet") or "")

    rec = find_manifest_by_doc_id(doc_id)
    source_url = rec.get("source_url") if isinstance(rec, dict) else None
    kind = rec.get("kind") if isinstance(rec, dict) else None
    case = rec.get("case") if isinstance(rec, dict) else None
    ts = rec.get("timestamp") if isinstance(rec, dict) else None

    domain = _domain_from_url(source_url) if source_url else ""

    if source_url:
        title = source_url
    elif domain:
        title = domain
    elif source_file:
        title = _summarize_path(source_file)
    else:
        title = _humanize_doc_id(doc_id)

    return {
        "doc_id": doc_id,
        "title": title,
        "domain": domain,
        "source_url": source_url,
        "source_file": source_file,
        "snippet": _clip_snippet(snippet),
        "score": hit.get("score"),
        "kind": kind,
        "case": case,
        "timestamp": ts,
    }

# -------------------------------------------------------------------
# Index helpers
# -------------------------------------------------------------------
def index_txt_document(txt_path: str | Path) -> None:
    update_index_for_file(Path(txt_path))

def _slug_from_url(url: str) -> str:
    parsed = urlparse(url)
    base = (parsed.netloc + parsed.path).lower()
    slug = re.sub(r"[^a-z0-9]+", "_", base).strip("_")
    return slug or "web_doc"

# -------------------------------------------------------------------
# PDF ingest
# -------------------------------------------------------------------
def ingest_source(url: str, case: str | None = None):
    _log_debug(f"ingest_source: START case={case!r}")
    _guard_remote_url(url)

    pdf_root = DATA_DIR / "web_pdfs"
    pdf_root.mkdir(parents=True, exist_ok=True)

    slug = _slug_from_url(url)
    pdf_path = pdf_root / f"{slug}.pdf"

    try:
        resp = requests.get(url, stream=True, timeout=(10, 60))
        resp.raise_for_status()
    except Exception as e:
        _log_debug(f"ingest_source: PDF download FAILED: {e!r}")
        raise RuntimeError(f"Failed to download PDF from {url}: {e}") from e

    with pdf_path.open("wb") as f:
        for chunk in resp.iter_content(chunk_size=8192):
            if chunk:
                f.write(chunk)

    OCR_DIR.mkdir(parents=True, exist_ok=True)
    txt_path = OCR_DIR / f"{slug}.txt"

    try:
        subprocess.run(
            ["pdftotext", "-layout", str(pdf_path), str(txt_path)],
            check=True,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
    except FileNotFoundError:
        raise RuntimeError("pdftotext not found. Install poppler-utils (sudo apt install poppler-utils).")
    except subprocess.CalledProcessError as e:
        _log_debug(f"ingest_source: pdftotext FAILED for {pdf_path}: {e!r}")
        raise RuntimeError(f"pdftotext failed for {pdf_path}: {e}") from e

    if not txt_path.exists():
        raise RuntimeError(f"pdftotext did not produce TXT file at {txt_path}")

    try:
        pdf_rel = pdf_path.relative_to(DATA_DIR)
    except ValueError:
        pdf_rel = pdf_path

    try:
        txt_rel = txt_path.relative_to(DATA_DIR)
    except ValueError:
        txt_rel = txt_path

    entry: dict[str, object | None] = {
        "kind": "web_pdf",
        "pdf": str(pdf_rel),
        "txt": str(txt_rel),
        "source_url": url,
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }
    if case:
        entry["case"] = case

    append_manifest_entry(entry)

    try:
        index_txt_document(txt_path)
    except Exception as e:
        _log_debug(f"ingest_source: index_txt_document FAILED for {txt_path}: {e!r}")

    return pdf_path, txt_path

# -------------------------------------------------------------------
# Text/manifest helpers
# -------------------------------------------------------------------
def _write_txt_and_manifest(text: str, url: str, case: str | None, kind: str) -> Path:
    OCR_DIR.mkdir(parents=True, exist_ok=True)
    slug = _slug_from_url(url)
    txt_path = OCR_DIR / f"{slug}.txt"
    txt_path.write_text(text, encoding="utf-8", errors="replace")

    try:
        txt_rel = txt_path.relative_to(DATA_DIR)
    except ValueError:
        txt_rel = txt_path

    entry: dict[str, object | None] = {
        "kind": kind,
        "pdf": None,
        "txt": str(txt_rel),
        "source_url": url,
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }
    if case:
        entry["case"] = case

    append_manifest_entry(entry)
    return txt_path

def _extract_docx_text(content: bytes) -> str:
    if Document is None:
        raise RuntimeError("python-docx not installed. pip install python-docx")
    with io.BytesIO(content) as buf:
        doc = Document(buf)
    parts: list[str] = []
    for para in doc.paragraphs:
        t = para.text.strip()
        if t:
            parts.append(t)
    return "\n".join(parts)

def _extract_csv_text(text: str) -> str:
    out_lines: list[str] = []
    reader = csv.reader(text.splitlines())
    for row in reader:
        out_lines.append("\t".join(cell.strip() for cell in row))
    return "\n".join(out_lines)

# -------------------------------------------------------------------
# Web ingest (used by worker)
# -------------------------------------------------------------------
def ingest_url_web(url: str, case: str | None):
    _log_debug(f"ingest_url_web: START case={case!r}")

    headers = {
        "User-Agent": "Mozilla/5.0 (StraightlineVault/0.1; +non-malicious investigative use)",
        "Accept": "*/*",
    }

    _guard_remote_url(url)

    try:
        resp = requests.get(url, headers=headers, timeout=(5, 10), allow_redirects=True)
        resp.raise_for_status()

        final_url = resp.url
        if final_url != url:
            _guard_remote_url(final_url)

        ctype = (resp.headers.get("Content-Type") or "").lower()
        _log_debug(f"ingest_url_web: GET ok status={resp.status_code} ctype={ctype}")

    except HTTPError as e:
        status = getattr(e.response, "status_code", None)
        host = urlparse(url).hostname or ""
        _log_debug(f"ingest_url_web: HTTPError status={status} host={host!r}")

        if status == 403 and HEADLESS_ON_403 and headless_can_use_for(host):
            _log_debug("ingest_url_web: attempting headless fetch on 403")
            final_url, html = headless_fetch_html(url)
            if final_url != url:
                _guard_remote_url(final_url)

            soup = BeautifulSoup(html, "html.parser")
            text = soup.get_text("\n", strip=True)
            txt_path = _write_txt_and_manifest(text, final_url, case, kind="web_html_headless")
            index_txt_document(str(txt_path))
            return None, txt_path

        raise RuntimeError(f"Network error fetching {url}: {e}") from e

    except Exception as e:
        _log_debug(f"ingest_url_web: GET FAILED: {e!r}")
        raise RuntimeError(f"Network error fetching {url}: {e}") from e

    final_url = resp.url
    ctype = (resp.headers.get("Content-Type") or "").lower()

    if "pdf" in ctype or final_url.lower().endswith(".pdf"):
        return ingest_source(final_url, case=case)

    if "officedocument.wordprocessingml.document" in ctype or final_url.lower().endswith(".docx"):
        text = _extract_docx_text(resp.content)
        txt_path = _write_txt_and_manifest(text, final_url, case, kind="web_docx")
        index_txt_document(str(txt_path))
        return None, txt_path

    if "text/csv" in ctype or final_url.lower().endswith(".csv"):
        text = _extract_csv_text(resp.text)
        txt_path = _write_txt_and_manifest(text, final_url, case, kind="web_csv")
        index_txt_document(str(txt_path))
        return None, txt_path

    if "html" in ctype or final_url.lower().endswith((".htm", ".html", "/")):
        soup = BeautifulSoup(resp.text, "html.parser")
        text = soup.get_text("\n", strip=True)
        txt_path = _write_txt_and_manifest(text, final_url, case, kind="web_html")
        index_txt_document(str(txt_path))
        return None, txt_path

    if ctype.startswith("text/"):
        txt_path = _write_txt_and_manifest(resp.text, final_url, case, kind="web_text")
        index_txt_document(str(txt_path))
        return None, txt_path

    raise ValueError(f"Unsupported content type for ingest: {ctype or 'unknown'}")

# -------------------------------------------------------------------
# Manifest / display helpers
# -------------------------------------------------------------------
def build_case_stats():
    stats = defaultdict(lambda: {"total": 0, "kinds": defaultdict(int)})
    for rec in iter_manifest() or []:
        case = rec.get("case") or "uncategorized"
        kind = rec.get("kind") or "unknown"
        stats[case]["total"] += 1
        stats[case]["kinds"][kind] += 1
    return sorted(
        ((name, {"total": info["total"], "kinds": dict(info["kinds"])}) for name, info in stats.items()),
        key=lambda x: x[0],
    )

def load_ocr_text(path_str: str):
    try:
        p = Path(path_str)
        if not p.is_absolute():
            p = DATA_DIR / p
        if not p.exists():
            legacy = ROOT / "ocr" / p.name
            if legacy.exists():
                p = legacy
        text = p.read_text(encoding="utf-8", errors="replace")
        return text, None
    except Exception as e:
        return None, str(e)

def build_url_to_doc_id_map() -> dict[str, str]:
    mapping: dict[str, str] = {}
    for rec in iter_manifest() or []:
        src = rec.get("source_url")
        txt = rec.get("txt")
        if not src or not txt:
            continue
        p = Path(str(txt))
        mapping.setdefault(str(src), p.stem)
    return mapping

def iter_recent_docs(limit: int = 50):
    records = list(iter_manifest() or [])

    def _parse_ts(rec):
        ts = rec.get("timestamp")
        if not ts:
            return datetime.min
        try:
            ts_norm = str(ts).replace("Z", "+00:00")
            dt = datetime.fromisoformat(ts_norm)
            if dt.tzinfo is not None:
                dt = dt.astimezone(timezone.utc).replace(tzinfo=None)
            return dt
        except Exception:
            return datetime.min

    records.sort(key=_parse_ts, reverse=True)
    docs = []
    for rec in records[:limit]:
        txt = rec.get("txt")
        if not txt:
            continue
        p = Path(str(txt))
        docs.append(
            {
                "doc_id": p.stem,
                "timestamp": rec.get("timestamp") or "",
                "case": rec.get("case"),
                "kind": rec.get("kind"),
                "source_url": rec.get("source_url"),
            }
        )
    return docs

# -------------------------------------------------------------------
# Routes: auth
# -------------------------------------------------------------------
@app.route("/login", methods=["GET", "POST"])
def login():
    next_url = _safe_next_url(request.args.get("next") or request.form.get("next") or "")
    if _is_logged_in():
        return redirect(next_url)

    error = None
    if request.method == "POST":
        _require_csrf()
        username = (request.form.get("username") or "").strip()
        password = request.form.get("password") or ""

        if verify_user(username, password):
            session.clear()
            session["auth"] = "1"
            session["user"] = username
            session["csrf_token"] = os.urandom(16).hex()
            session.permanent = True
            return redirect(next_url)

        error = "Invalid username or password."

    csrf_token = _get_csrf_token()
    return render_template("login.html", error=error, next=next_url, csrf_token=csrf_token)

@app.route("/signup", methods=["GET", "POST"])
def signup():
    # If logged in already, don't allow casual account creation
    if _is_logged_in():
        return redirect(url_for("vault_search"))

    error = None
    if request.method == "POST":
        _require_csrf()
        invite = (request.form.get("invite") or "").strip()
        username = (request.form.get("username") or "").strip()
        password = request.form.get("password") or ""

        try:
            consume_invite(invite, username, password)
            return redirect(url_for("login"))
        except Exception as e:
            error = str(e)

    csrf_token = _get_csrf_token()
    return render_template("signup.html", error=error, csrf_token=csrf_token)

@app.route("/logout", methods=["POST"])
@require_access
def logout():
    _require_csrf()
    session.clear()
    return redirect(url_for("login"))

# -------------------------------------------------------------------
# Routes: app
# -------------------------------------------------------------------
@app.route("/", methods=["GET"])
def index_redirect():
    if request.query_string:
        return redirect(url_for("vault_search") + "?" + request.query_string.decode("utf-8"))
    return redirect(url_for("vault_search"))

@app.route("/web-ingest", methods=["GET", "POST"])
@require_access
def web_ingest():
    if request.method == "POST":
        _require_csrf()

    active_nav = "web_ingest"
    query: str = ""
    case: str = ""
    limit: int = 10
    error: str | None = None

    search_results: list[dict] = []
    ingested: list[dict] = []
    ingested_by_url: dict[str, dict] = {}

    if request.method == "POST":
        query = (request.form.get("query") or "").strip()
        case = (request.form.get("case") or "").strip()
        limit_raw = (request.form.get("limit") or "").strip()

        try:
            limit = int(limit_raw) if limit_raw else 10
        except ValueError:
            error = "Limit must be an integer."
            limit = 10

        limit = max(1, min(limit, 20))

        if not query and not error:
            error = "Query is required."

        if not error:
            if not case:
                slug = re.sub(r"[^a-z0-9]+", "_", query.lower()).strip("_")
                case = f"{slug}_web" if slug else "web"

            try:
                raw_results = metasearch(query, max_results=limit)
            except Exception as e:
                current_app.logger.exception("web_ingest: metasearch error")
                error = f"Metasearch error: {e}"
                raw_results = []

            for r in raw_results:
                title = (r.get("title") if isinstance(r, dict) else getattr(r, "title", "")) or ""
                url = (r.get("url") if isinstance(r, dict) else getattr(r, "url", "")) or ""
                snippet = (r.get("snippet") if isinstance(r, dict) else getattr(r, "snippet", "")) or ""
                engine = (r.get("engine") if isinstance(r, dict) else getattr(r, "engine", "")) or ""
                if not url:
                    continue
                search_results.append({"title": title, "url": url, "snippet": snippet, "engine": engine})

            if not error and not search_results:
                error = "No results returned from metasearch."

            if not error:
                JOBS_ROOT = Path(os.getenv("STRAIGHTLINE_JOBS_DIR", "/opt/straightline-vault/jobs"))
                JOBS_QUEUE_DIR = JOBS_ROOT / "queue"
                JOBS_QUEUE_DIR.mkdir(parents=True, exist_ok=True)

                for hit in search_results:
                    url = hit["url"]
                    try:
                        ts = int(time.time() * 1000)
                        pid = os.getpid()
                        safe_case = re.sub(r"[^a-z0-9]+", "_", (case or "web").lower()).strip("_")
                        job_name = f"{ts}-{pid}-{safe_case}.json"
                        job_path = JOBS_QUEUE_DIR / job_name
                        payload = {"url": url, "case": case}
                        job_path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
                        item = {"url": url, "job_path": str(job_path), "error": None}
                    except Exception as e:
                        item = {"url": url, "job_path": None, "error": str(e)}
                    ingested.append(item)
                    ingested_by_url[url] = item

    queue_jobs: list[dict] = []
    try:
        JOBS_ROOT = Path(os.getenv("STRAIGHTLINE_JOBS_DIR", "/opt/straightline-vault/jobs"))
        JOBS_QUEUE_DIR = JOBS_ROOT / "queue"
        if JOBS_QUEUE_DIR.exists():
            for p in sorted(JOBS_QUEUE_DIR.glob("*.json")):
                name = p.name
                ts_ms = None
                parts = name.split("-", 2)
                if parts:
                    try:
                        ts_ms = int(parts[0])
                    except Exception:
                        ts_ms = None
                queued_at = ""
                if ts_ms is not None:
                    try:
                        queued_at = datetime.utcfromtimestamp(ts_ms / 1000.0).isoformat()
                    except Exception:
                        queued_at = ""

                url = ""
                case_field = None
                try:
                    payload = json.loads(p.read_text(encoding="utf-8"))
                    url = str(payload.get("url") or "")
                    case_field = payload.get("case")
                except Exception:
                    pass

                queue_jobs.append({"name": name, "url": url, "case": case_field, "queued_at": queued_at})
    except Exception as e:
        _log_debug(f"web_ingest: failed to list queue jobs: {e!r}")

    url_to_doc_id = build_url_to_doc_id_map()
    csrf_token = _get_csrf_token()

    return render_template(
        "web_ingest.html",
        active_nav=active_nav,
        query=query,
        case=case,
        limit=limit,
        error=error,
        search_results=search_results,
        ingested=ingested,
        ingested_by_url=ingested_by_url,
        queue_jobs=queue_jobs,
        url_to_doc_id=url_to_doc_id,
        csrf_token=csrf_token,
    )

@app.route("/cases", methods=["GET"])
@require_access
def cases_view():
    raw_stats = build_case_stats()
    cases = []
    for case_name, info in raw_stats:
        kinds_dict = info.get("kinds", {}) or {}
        kinds_list = [f"{k}={v}" for k, v in kinds_dict.items()]
        cases.append({"name": case_name, "doc_count": info.get("total", 0), "kinds": kinds_list})
    return render_template("cases.html", cases=cases, active_nav="cases")

@app.route("/recent", methods=["GET"])
@require_access
def recent_view():
    docs = iter_recent_docs(limit=50)
    return render_template("recent.html", docs=docs, active_nav="recent")

@app.route("/cases/<case_name>", methods=["GET"])
@require_access
def case_view(case_name: str):
    def _domain(u: str) -> str:
        try:
            return (urlparse(u).netloc or "").lower()
        except Exception:
            return ""

    docs = []
    for rec in (iter_manifest() or []):
        rec_case = (rec.get("case") or "uncategorized")
        if rec_case != case_name:
            continue

        txt = rec.get("txt")
        pdf = rec.get("pdf")
        source_url = rec.get("source_url")

        doc_id = None
        if txt:
            try:
                doc_id = Path(str(txt)).stem
            except Exception:
                doc_id = None
        if not doc_id and pdf:
            try:
                doc_id = Path(str(pdf)).stem
            except Exception:
                doc_id = None
        if not doc_id and source_url:
            doc_id = (
                source_url.replace("https://", "")
                .replace("http://", "")
                .replace("/", "_")
                .replace("?", "_")
                .replace("&", "_")
                .replace("=", "_")
                .replace("#", "_")
            )[:120]

        docs.append(
            {
                "doc_id": doc_id or "(unknown)",
                "timestamp": rec.get("timestamp"),
                "kind": rec.get("kind"),
                "pdf": pdf,
                "source_url": source_url,
                "domain": _domain(source_url) if source_url else "",
                "has_text": bool(txt),
            }
        )

    docs.sort(key=lambda d: d.get("timestamp") or "", reverse=True)
    return render_template("case.html", case_name=case_name, docs=docs, active_nav="cases")

@app.route("/doc/<doc_id>", methods=["GET"])
@require_access
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

@app.route("/vault-search", methods=["GET", "POST"])
@app.route("/search", methods=["GET", "POST"])
@require_access
def vault_search():
    query = (request.form.get("q") or request.form.get("query") or "").strip()
    if not query:
        query = (request.args.get("q") or request.args.get("query") or "").strip()

    def _clamp_int(val, default, lo, hi):
        try:
            n = int(val)
        except Exception:
            n = default
        return max(lo, min(hi, n))

    page = _clamp_int(request.args.get("page"), 1, 1, 10_000)
    per_page = _clamp_int(request.args.get("per_page"), 10, 5, 50)

    error = None
    results = []
    showing_from = 0
    showing_to = 0
    has_prev = False
    has_next = False

    if query:
        try:
            fetch_limit = page * per_page
            raw_hits = run_search(query, limit=fetch_limit)

            start = (page - 1) * per_page
            end = start + per_page
            page_hits = raw_hits[start:end]

            results = [_decorate_hit(h) for h in page_hits]

            has_prev = page > 1
            has_next = len(raw_hits) > end
            if results:
                showing_from = start + 1
                showing_to = start + len(results)

        except Exception as e:
            current_app.logger.exception("vault_search: run_search failed")
            error = f"Search error: {e}"

    return render_template(
        "search.html",
        query=query,
        results=results,
        error=error,
        active_nav="search",
        page=page,
        per_page=per_page,
        has_prev=has_prev,
        has_next=has_next,
        showing_from=showing_from,
        showing_to=showing_to,
    )
