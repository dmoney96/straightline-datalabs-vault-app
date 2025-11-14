from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI, HTTPException, Query
from pydantic import BaseModel
from whoosh.index import open_dir
from whoosh.qparser import QueryParser
from whoosh import scoring

from vault_core.paths import OUTPUT_DIR
from vault_core.logging_config import get_logger


INDEX_DIR = OUTPUT_DIR / "index"
logger = get_logger("api")

app = FastAPI(
    title="StraightLine DataLabs Vault API",
    version="0.1.0",
    description="Search endpoint for the StraightLine vault index.",
)


class Hit(BaseModel):
    doc_id: str
    source_file: str
    score: float
    snippet: str


class SearchResponse(BaseModel):
    query: str
    total: int
    results: list[Hit]


@app.get("/health")
def health() -> dict:
    """Simple healthcheck."""
    return {"status": "ok"}


@app.get("/search", response_model=SearchResponse)
def search(
    q: str = Query(..., min_length=1, description="Search query string"),
    limit: int = Query(10, ge=1, le=50, description="Max results"),
) -> SearchResponse:
    """Full-text search over the vault index."""
    if not INDEX_DIR.exists():
        logger.warning("Search requested but index directory %s is missing", INDEX_DIR)
        raise HTTPException(status_code=503, detail="Index not built yet.")

    try:
        ix = open_dir(INDEX_DIR)
    except Exception as e:  # noqa: BLE001
        logger.exception("Failed to open index: %s", e)
        raise HTTPException(status_code=500, detail="Failed to open index.")

    hits: list[Hit] = []

    with ix.searcher(weighting=scoring.BM25F()) as searcher:
        qp = QueryParser("content", schema=ix.schema)
        query = qp.parse(q)
        results = searcher.search(query, limit=limit)

        for hit in results:
            snippet = hit.highlights("content", top=2) or hit.get("content", "")[:200]
            hits.append(
                Hit(
                    doc_id=hit["doc_id"],
                    source_file=hit["source_file"],
                    score=float(hit.score),
                    snippet=snippet,
                )
            )

    logger.info("Search q=%r -> %d hits", q, len(hits))
    return SearchResponse(query=q, total=len(hits), results=hits)
