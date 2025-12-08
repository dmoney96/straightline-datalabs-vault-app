from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import List, Optional

from whoosh import index, qparser, scoring, query as q, highlight
from whoosh.analysis import StandardAnalyzer, NgramFilter
from whoosh.fields import Schema, ID, KEYWORD, TEXT

from vault_core.paths import DATA_DIR
from vault_core.manifest import iter_manifest

import re
import unicodedata

WHITESPACE_RE = re.compile(r"\s+")


def normalize_query(q: str) -> str:
    """
    Normalize user query text so small formatting differences
    don't break matching.

    - NFKC normalize unicode
    - convert smart quotes / dashes
    - collapse whitespace
    """
    if not q:
        return ""

    q_norm = unicodedata.normalize("NFKC", q)

    # Smart quotes -> plain
    q_norm = (
        q_norm.replace("“", '"')
        .replace("”", '"')
        .replace("‘", "'")
        .replace("’", "'")
    )

    # Fancy dashes -> simple hyphen
    q_norm = q_norm.replace("—", "-").replace("–", "-")

    # Collapse whitespace
    q_norm = WHITESPACE_RE.sub(" ", q_norm)

    return q_norm.strip()

# Index lives under DATA_DIR/index
INDEX_DIR = DATA_DIR / "index"

# Analyzer: normal tokenization + 3-15 char n-grams for partial matches
content_analyzer = StandardAnalyzer() | NgramFilter(minsize=3, maxsize=15)

schema = Schema(
    doc_id=ID(stored=True, unique=True),
    case=KEYWORD(stored=True, commas=True, lowercase=True, scorable=False),
    kind=KEYWORD(stored=True, commas=True, lowercase=True, scorable=False),
    source=ID(stored=True),
    content=TEXT(stored=True, analyzer=content_analyzer),
)


@dataclass
class SearchResult:
    doc_id: str
    score: float
    snippet: str
    source: Optional[str]


def _ensure_index() -> index.Index:
    """
    Open the Whoosh index, creating it if needed.
    """
    INDEX_DIR.mkdir(parents=True, exist_ok=True)
    if index.exists_in(INDEX_DIR):
        return index.open_dir(INDEX_DIR)
    return index.create_in(INDEX_DIR, schema)


def _metadata_for_txt(txt_path: str) -> dict:
    """
    Look up case/kind/source_url in the manifest for this txt path, if any.

    Works whether manifest stores txt as relative to DATA_DIR or as a raw path.
    """
    p = Path(txt_path)

    # If txt_path is inside DATA_DIR, compute a relative version; otherwise None.
    try:
        rel = p.relative_to(DATA_DIR)
    except Exception:
        rel = None

    for rec in iter_manifest() or []:
        rec_txt = rec.get("txt")
        if not rec_txt:
            continue

        rp = Path(rec_txt)

        # If manifest is storing txt as relative to DATA_DIR
        if rel is not None and rp == rel:
            return {
                "case": rec.get("case"),
                "kind": rec.get("kind"),
                "source": rec.get("source_url") or rec.get("pdf") or None,
            }

        # If manifest is storing txt as whatever path we hand to indexer
        if rel is None and rp == p:
            return {
                "case": rec.get("case"),
                "kind": rec.get("kind"),
                "source": rec.get("source_url") or rec.get("pdf") or None,
            }

    return {"case": None, "kind": None, "source": None}


def index_txt_document(txt_path: str) -> None:
    """
    Index a single TXT document into Whoosh.

    - doc_id is the stem of the filename
    - content is the full text of the TXT file
    - case/kind/source come from manifest if available
    """
    p = Path(txt_path)
    doc_id = p.stem
    text = p.read_text(encoding="utf-8", errors="replace")

    meta = _metadata_for_txt(txt_path)

    ix = _ensure_index()
    writer = ix.writer()
    try:
        writer.update_document(
            doc_id=doc_id,
            case=(meta.get("case") or "").lower(),
            kind=(meta.get("kind") or "").lower(),
            source=meta.get("source") or "",
            content=text,
        )
    finally:
        writer.commit()

import re
import unicodedata

def run_search(
    query: str,
    case: Optional[str] = None,
    kind: Optional[str] = None,
    limit: int = 20,
) -> List[SearchResult]:
    """
    Run a BM25F search over the index.

    - query: user query string
    - case/kind: optional filters (exact match, case-insensitive)
    - limit: max number of hits
    """
    # NEW: normalize and early-exit on empty
    query = normalize_query(query or "")
    if not query:
        return []

    ix = _ensure_index()

    with ix.searcher(
        weighting=scoring.BM25F(field_B={"content": 0.75}, K1=1.5)
    ) as searcher:
        parser = qparser.MultifieldParser(
            ["content", "doc_id", "source"],
            schema=ix.schema,
            group=qparser.OrGroup.factory(0.9),
        )
        parser.add_plugins(
            [
                qparser.FuzzyTermPlugin(),
                qparser.PhrasePlugin(),
            ]
        )

        parsed = parser.parse(query)

        # Optional filters: case + kind
        filters: List[q.Query] = []
        if case:
            filters.append(q.Term("case", case.lower()))
        if kind:
            filters.append(q.Term("kind", kind.lower()))

        if filters:
            parsed = q.And([parsed] + filters)

        hits = searcher.search(parsed, limit=limit)
        hits.fragmenter = highlight.ContextFragmenter(
            maxchars=220,
            surround=60,
        )

        results: List[SearchResult] = []
        for h in hits:
            snippet = h.highlights("content") or ""
            results.append(
                SearchResult(
                    doc_id=h["doc_id"],
                    score=h.score,
                    snippet=snippet,
                    source=h.get("source"),
                )
            )
        return results
