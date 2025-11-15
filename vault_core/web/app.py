from __future__ import annotations

import sys
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional

from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import HTMLResponse
from pydantic import BaseModel

from whoosh.index import open_dir, EmptyIndexError
from whoosh.qparser import QueryParser
from whoosh import scoring

# Ensure project root is importable
ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from vault_core.paths import OUTPUT_DIR, OCR_DIR  # noqa: E402
from vault_core.logging_config import get_logger  # noqa: E402
from vault_core.manifest import iter_records  # noqa: E402


app = FastAPI(
    title="StraightLine Data Vault API",
)

logger = get_logger("api")
INDEX_DIR = OUTPUT_DIR / "index"


# ---------- Pydantic models ----------


class SearchHit(BaseModel):
    doc_id: str
    source_file: str
    score: float
    snippet: str
    tenant_id: str
    ingest_type: str


class SearchResponse(BaseModel):
    query: str
    total: int
    results: List[SearchHit]


class DocSummary(BaseModel):
    doc_id: str
    tenant_id: str
    ingest_type: str
    source_file: str
    source_url: Optional[str] = None
    tags: List[str] = []
    collection: Optional[str] = None
    created_at: Optional[str] = None


class DocsResponse(BaseModel):
    total: int
    results: List[DocSummary]


class DocDetail(DocSummary):
    text_preview: Optional[str] = None


# ---------- Helpers ----------


def _load_manifest_index() -> Dict[str, Dict[str, Any]]:
    """Return manifest records keyed by doc_id."""
    by_id: Dict[str, Dict[str, Any]] = {}
    for rec in iter_records():
        doc_id = rec.get("doc_id")
        if not doc_id:
            continue
        by_id[doc_id] = rec
    return by_id


def _record_matches_filters(
    rec: Dict[str, Any],
    tenant_id: Optional[str],
    tags: Optional[List[str]],
    collection: Optional[str],
) -> bool:
    if tenant_id and rec.get("tenant_id", "default") != tenant_id:
        return False

    extra = rec.get("extra") or {}

    if collection and extra.get("collection") != collection:
        return False

    if tags:
        record_tags = set(extra.get("tags") or [])
        if not set(tags).issubset(record_tags):
            return False

    return True


def _open_index():
    if not INDEX_DIR.exists():
        raise RuntimeError(f"Index directory {INDEX_DIR} does not exist")
    return open_dir(str(INDEX_DIR))


# ---------- HTML home ----------


@app.get("/", response_class=HTMLResponse)
def home() -> str:
    return """
<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8" />
  <title>StraightLine Data Vault</title>
  <style>
    body { font-family: system-ui, -apple-system, BlinkMacSystemFont, sans-serif;
           max-width: 900px; margin: 2rem auto; padding: 0 1rem; }
    h1 { margin-bottom: 0.5rem; }
    .search-box { margin: 1rem 0; display: flex; gap: 0.5rem; }
    input[type="text"] { flex: 1; padding: 0.4rem 0.6rem; }
    button { padding: 0.4rem 0.8rem; cursor: pointer; }
    .result { border: 1px solid #ddd; border-radius: 6px; padding: 0.75rem;
              margin-bottom: 0.75rem; }
    .meta { font-size: 0.8rem; color: #666; }
    pre { white-space: pre-wrap; font-size: 0.9rem; }
    .badge { display: inline-block; padding: 0.1rem 0.4rem; margin-right: 0.25rem;
             border-radius: 999px; background: #eee; font-size: 0.75rem; }
  </style>
</head>
<body>
  <h1>StraightLine Data Vault</h1>
  <p>A tiny, sharp research index under construction. 🔍</p>

  <div class="search-box">
    <input id="q" type="text" placeholder="Search (e.g. 'travel expenses')" />
    <button onclick="runSearch()">Search</button>
  </div>

  <div id="status"></div>
  <div id="results"></div>

<script>
async function runSearch() {
  const q = document.getElementById('q').value;
  const status = document.getElementById('status');
  const resultsDiv = document.getElementById('results');
  status.textContent = 'Searching...';
  resultsDiv.innerHTML = '';

  try {
    const resp = await fetch('/search?q=' + encodeURIComponent(q));
    if (!resp.ok) {
      status.textContent = 'Error: ' + resp.status;
      return;
    }
    const data = await resp.json();
    status.textContent = 'Found ' + data.total + ' result(s).';

    for (const hit of data.results) {
      const div = document.createElement('div');
      div.className = 'result';
      div.innerHTML = `
        <div><strong>${hit.doc_id}</strong></div>
        <div class="meta">
          tenant=${hit.tenant_id} · type=${hit.ingest_type}<br/>
          <small>${hit.source_file}</small>
        </div>
        <div>${hit.snippet}</div>
      `;
      resultsDiv.appendChild(div);
    }
  } catch (e) {
    console.error(e);
    status.textContent = 'Error: ' + e;
  }
}
</script>
</body>
</html>
    """


# ---------- API endpoints ----------


@app.get("/search", response_model=SearchResponse)
def search(
    q: str = Query(..., min_length=1),
    limit: int = Query(20, ge=1, le=100),
    tenant_id: Optional[str] = Query(None),
    tags: Optional[List[str]] = Query(None),
    collection: Optional[str] = Query(None),
) -> SearchResponse:
    logger.info(
        "Search q=%r limit=%d tenant_id=%r tags=%r collection=%r",
        q, limit, tenant_id, tags, collection,
    )

    manifest_by_id = _load_manifest_index()

    try:
        ix = _open_index()
    except Exception as e:
        logger.exception("Failed to open index: %r", e)
        raise HTTPException(status_code=500, detail="Index not available")

    with ix.searcher(weighting=scoring.BM25F()) as searcher:
        parser = QueryParser("content", schema=ix.schema)
        query_obj = parser.parse(q)
        hits = searcher.search(query_obj, limit=limit)

        results: List[SearchHit] = []

        for hit in hits:
            doc_id = hit.get("doc_id")
            if not doc_id:
                continue

            rec = manifest_by_id.get(doc_id, {})

            # Apply manifest-level filters
            if not _record_matches_filters(rec, tenant_id, tags, collection):
                if tenant_id or tags or collection:
                    # If the caller asked for filters and this record
                    # doesn't match, skip it.
                    continue

            snippet = hit.highlights("content", top=2)
            if not snippet:
                content = hit.get("content", "")
                snippet = content[:240]

            results.append(
                SearchHit(
                    doc_id=doc_id,
                    source_file=hit.get("source_file", rec.get("source_file", "")),
                    score=float(hit.score),
                    snippet=snippet,
                    tenant_id=rec.get("tenant_id", "default"),
                    ingest_type=rec.get("ingest_type", "unknown"),
                )
            )

    return SearchResponse(query=q, total=len(results), results=results)


@app.get("/docs", response_model=DocsResponse)
def list_docs(
    tenant_id: Optional[str] = Query(None),
    tags: Optional[List[str]] = Query(None),
    collection: Optional[str] = Query(None),
) -> DocsResponse:
    manifest_by_id = _load_manifest_index()

    results: List[DocSummary] = []

    for doc_id, rec in manifest_by_id.items():
        if not _record_matches_filters(rec, tenant_id, tags, collection):
            continue

        extra = rec.get("extra") or {}

        results.append(
            DocSummary(
                doc_id=doc_id,
                tenant_id=rec.get("tenant_id", "default"),
                ingest_type=rec.get("ingest_type", "unknown"),
                source_file=rec.get("source_file", ""),
                source_url=rec.get("source_url"),
                tags=extra.get("tags") or [],
                collection=extra.get("collection"),
                created_at=rec.get("created_at"),
            )
        )

    return DocsResponse(total=len(results), results=results)


@app.get("/doc/{doc_id}", response_model=DocDetail)
def get_doc(doc_id: str) -> DocDetail:
    manifest_by_id = _load_manifest_index()
    rec = manifest_by_id.get(doc_id)
    if not rec:
        raise HTTPException(status_code=404, detail=f"Document {doc_id!r} not found")

    extra = rec.get("extra") or {}

    # Try grabbing OCR text preview, if available
    text_preview: Optional[str] = None
    try:
        txt_path = OCR_DIR / f"{doc_id}.txt"
        if txt_path.exists():
            text_preview = txt_path.read_text(errors="ignore")[:2000]
    except Exception:
        # Best-effort; do not fail the endpoint on preview issues
        pass

    return DocDetail(
        doc_id=doc_id,
        tenant_id=rec.get("tenant_id", "default"),
        ingest_type=rec.get("ingest_type", "unknown"),
        source_file=rec.get("source_file", ""),
        source_url=rec.get("source_url"),
        tags=extra.get("tags") or [],
        collection=extra.get("collection"),
        created_at=rec.get("created_at"),
        text_preview=text_preview,
    )


@app.get("/health")
def health() -> Dict[str, Any]:
    problems: List[str] = []

    # Check manifest has at least one record
    manifest_records = list(iter_records())
    if not manifest_records:
        problems.append("Manifest is empty")

    # Check index directory
    if not INDEX_DIR.exists():
        problems.append(f"Index directory {INDEX_DIR} does not exist")
    else:
        try:
            ix = open_dir(str(INDEX_DIR))
            with ix.searcher() as s:
                _ = s.doc_count()  # touch the index
        except EmptyIndexError:
            problems.append(f"Index in {INDEX_DIR} is empty or invalid")
        except Exception as e:
            problems.append(f"Error opening index: {e!r}")

    status = "ok" if not problems else "degraded"
    if problems:
        logger.error("Health check issues: %s", problems)
    else:
        logger.info("Health check OK")

    return {
        "status": status,
        "problems": problems,
        "index_dir": str(INDEX_DIR),
    }
