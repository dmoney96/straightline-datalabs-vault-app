from fastapi import FastAPI
from whoosh.index import open_dir
from whoosh.qparser import MultifieldParser
from whoosh import scoring

from vault_core.paths import INDEX_DIR

app = FastAPI(title="Straightline Vault API")

@app.get("/search")
def search(q: str):
    ix = open_dir(INDEX_DIR)
    parser = MultifieldParser(["content", "source_file"], schema=ix.schema)

    with ix.searcher(weighting=scoring.BM25F()) as searcher:
        query = parser.parse(q)
        results = searcher.search(query, limit=20)

        response = []
        for hit in results:
            response.append({
                "score": float(hit.score),
                "doc_id": hit["doc_id"],
                "source_file": hit["source_file"],
                "snippet": hit.highlights("content") or ""
            })

        return response

@app.get("/")
def root():
    return {"message": "Straightline Vault API running"}
