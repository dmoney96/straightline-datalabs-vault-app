from fastapi import FastAPI, Query, HTTPException
from pydantic import BaseModel
from whoosh import index
from whoosh.qparser import MultifieldParser, OrGroup
from whoosh.highlight import UppercaseFormatter

from paths import OUTPUT_DIR

app = FastAPI(title="Straightline Vault Search API")

INDEX_DIR = OUTPUT_DIR / "index"


class SearchHit(BaseModel):
    doc_id: str
    source_file: str
    score: float
    snippet: str | None = None


class SearchResponse(BaseModel):
    query: str
    total: int
    hits: list[SearchHit]


def get_index():
    try:
        return index.open_dir(INDEX_DIR)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Index not available: {e}")


@app.get("/health")
def health():
    return {"status": "ok", "index_path": str(INDEX_DIR)}


@app.get("/search", response_model=SearchResponse)
def search(q: str = Query(..., min_length=1, max_length=200)):
    ix = get_index()
    with ix.searcher() as searcher:
        parser = MultifieldParser(["content"], schema=ix.schema, group=OrGroup)
        query = parser.parse(q)
        results = searcher.search(query, limit=10)

        # nicer snippets
        results.fragmenter.charlimit = None
        results.formatter = UppercaseFormatter()

        hits: list[SearchHit] = []
        for hit in results:
            snippet = hit.highlights("content", top=2) or ""
            hits.append(
                SearchHit(
                    doc_id=hit["doc_id"],
                    source_file=hit["source_file"],
                    score=float(hit.score),
                    snippet=snippet,
                )
            )

        return SearchResponse(query=q, total=len(results), hits=hits)
