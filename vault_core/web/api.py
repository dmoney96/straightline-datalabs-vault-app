from __future__ import annotations

from typing import List, Optional

from fastapi import APIRouter, Query
from pydantic import BaseModel

from vault_core.manifest import iter_manifest
from vault_core.search.indexer import search_index


router = APIRouter()


# ------------------------------
# Models
# ------------------------------

class ManifestRecord(BaseModel):
    kind: Optional[str] = None
    source_url: Optional[str] = None
    pdf: Optional[str] = None
    txt: Optional[str] = None
    timestamp: Optional[str] = None


class SearchHit(BaseModel):
    score: float
    doc_id: str
    source_file: str
    snippet: str


# ------------------------------
# Endpoints
# ------------------------------

@router.get("/health")
async def api_health():
    """Simple API-level health check."""
    return {"api": "ok"}


@router.get("/manifest", response_model=List[ManifestRecord])
def manifest_tail(limit: int = Query(20, ge=1, le=200)):
    """
    Return the last N manifest entries.
    """
    entries = list(iter_manifest())
    tail = entries[-limit:]
    return [ManifestRecord(**rec) for rec in tail]


@router.get("/search", response_model=List[SearchHit])
def api_search(q: str = Query(..., min_length=1)):
    """
    Search the current text index for keyword(s).
    """
    hits = search_index(q)
    results = [
        SearchHit(
            score=h["score"],
            doc_id=h["doc_id"],
            source_file=h["source_file"],
            snippet=h["snippet"],
        )
        for h in hits
    ]
    return results


__all__ = ["router"]
