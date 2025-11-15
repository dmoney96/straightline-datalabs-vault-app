from __future__ import annotations

import sys
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional

from fastapi import FastAPI, HTTPException, Query
from pydantic import BaseModel

from whoosh.index import open_dir, EmptyIndexError
from whoosh.qparser import QueryParser
from whoosh import scoring

# Ensure project root is importable
ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from vault_core.paths import OUTPUT_DIR  # noqa: E402
from vault_core.logging_config import get_logger  # noqa: E402
from vault_core.manifest import iter_records  # noqa: E402


app = FastAPI(
    title="StraightLine Data Vault API",
    version="0.1.0",
    description="Search + document metadata API for StraightLine Vault",
)

logger = get_logger("api")

INDEX_DIR = OUTPUT_DIR / "index"


# ---------- Pydantic models ----------


class SearchHit(BaseModel):
    doc_id: str
    source_file: str
    score: float
    snippet: str
    tenant_id: str = "default"
    ingest_type: str = "unknown"


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


def _open_index():
    try:
        return open_dir(str(INDEX_DIR))
    except EmptyIndexError:
        logger.error("Index directory %s does not contain a valid index", INDEX_DIR)
        raise HTTPException(status_code=500, detail="Search index is empty or missing")


def _manifest_index() -> Dict[str, Dict[str, Any]]:
    """
    Build a mapping doc_id -> manifest record for quick lookup.
    """
    records: Dict[str, Dict[str, Any]] = {}
    for rec in iter_records():
        doc_id = rec.get("doc_id")
        if not doc_id:
            continue
        records[doc_id] = rec
    return records


def _record_matches_filters(
    rec: Dict[str, Any],
    tenant_id: Optional[str],
    tags: Optional[List[str]],
    collection: Optional[str],
) -> bool:
    if tenant_id is not None:
        if rec.get("tenant_id", "default") != tenant_id:
            return False

    extra = rec.get("extra") or {}

    if tags:
        rec_tags = set(extra.get("tags") or [])
        if not set(tags).issubset(rec_tags):
            return False

    if collection is not None:
        if extra.get("collection") != collection:
            return False

    return True


# ---------- Endpoints ----------


@app.get("/search", response_model=SearchResponse)
def search(
    q: str = Query(..., alias="q", description="Full-text search query"),
    limit: int = Query(20, ge=1, le=100),
    tenant_id: Optional[str] = Query(
        None, description="Optional tenant filter (future multi-tenant support)"
    ),
    tags: Optional[List[str]] = Query(
        None,
        description="Filter by tags (AND semantics: doc must have all specified tags)",
    ),
    collection: Optional[str] = Query(
        None, description="Filter by logical collection/bucket name"
    ),
):
    logger.info(
        "Search q=%r limit=%d tenant_id=%r tags=%r collection=%r",
        q,
        limit,
        tenant_id,
        tags,
        collection,
    )

    ix = _open_index()
    manifest_by_id = _manifest_index()

    parser = QueryParser("content", schema=ix.schema)
    query = parser.parse(q)

    results: List[SearchHit] = []

    with ix.searcher(weighting=scoring.BM25F()) as searcher:
        hits = searcher.search(query, limit=limit)

        for hit in hits:
            doc_id = hit["doc_id"]
            rec = manifest_by_id.get(doc_id)

            if rec and not _record_matches_filters(rec, tenant_id, tags, collection):
                continue

            snippet = hit.highlights("content", top=2) or hit.get("content", "")[:200]
            source_file = hit.get("source_file", "")

            ingest_type = "unknown"
            tenant = "default"
            if rec:
                ingest_type = rec.get("ingest_type", ingest_type)
                tenant = rec.get("tenant_id", tenant)

            results.append(
                SearchHit(
                    doc_id=doc_id,
                    source_file=source_file,
                    score=float(hit.score),
                    snippet=snippet,
                    ingest_type=ingest_type,
                    tenant_id=tenant,
                )
            )

    return SearchResponse(query=q, total=len(results), results=results)


@app.get("/docs", response_model=DocsResponse)
def list_docs(
    tenant_id: Optional[str] = Query(
        None, description="Optional tenant filter for documents"
    ),
    tags: Optional[List[str]] = Query(
        None, description="Filter docs by tags (AND semantics)"
    ),
    collection: Optional[str] = Query(
        None, description="Filter docs by collection (e.g. 'tax-guides-2025')"
    ),
):
    """
    Return a summary list of all known documents from the manifest.
    """
    logger.info(
        "List docs tenant_id=%r tags=%r collection=%r",
        tenant_id,
        tags,
        collection,
    )

    manifest_by_id = _manifest_index()
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
def get_doc(doc_id: str):
    """
    Return full manifest metadata + an optional text_preview if we can
    find an OCR'd .txt.
    """
    manifest_by_id = _manifest_index()
    rec = manifest_by_id.get(doc_id)

    if not rec:
        raise HTTPException(status_code=404, detail=f"Document {doc_id!r} not found")

    extra = rec.get("extra") or {}

    text_preview: Optional[str] = None
    try:
        # Try to guess an OCR file next to source_file, but fail softly.
        if "source_file" in rec and rec["source_file"]:
            src_path = Path(rec["source_file"])
            txt_candidate = src_path.with_suffix(".txt")
            if txt_candidate.exists():
                text_preview = txt_candidate.read_text(errors="ignore")
    except Exception:
        # We don't want preview errors to break the API
        logger.exception("Failed to load text_preview for doc_id=%s", doc_id)

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

@app.get("/")
def root():
    """
    Simple root so hitting the base URL doesn't 404.
    """
    return {
        "status": "ok",
        "service": "StraightLine Data Vault API",
        "version": "0.1.0",
        "endpoints": [
            "/search",
            "/docs",
            "/doc/{doc_id}",
            "/health",
            "/docs (Swagger UI)",
            "/openapi.json",
        ],
    }


@app.get("/health")
def health():
    """
    Minimal health check.

    Later we can expand this to verify:
      - disk space
      - index freshness
      - background workers, etc.
    """
    problems = []

    # Check index directory
    if not INDEX_DIR.exists():
        problems.append(f"Index directory {INDEX_DIR} does not exist")
    else:
        try:
            _ = open_dir(str(INDEX_DIR))
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
