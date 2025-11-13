import sys
from whoosh.index import open_dir
from whoosh.qparser import QueryParser
from whoosh import scoring

from paths import OUTPUT_DIR

INDEX_DIR = OUTPUT_DIR / "index"


def main():
    if len(sys.argv) < 2:
        print("Usage: python scripts/search_cli.py <query terms...>")
        sys.exit(1)

    query_text = " ".join(sys.argv[1:])
    print(f"🔎 Searching for: {query_text!r}\n")

    ix = open_dir(INDEX_DIR)

    with ix.searcher(weighting=scoring.BM25F()) as searcher:
        # Our schema has a 'content' field; that's what we search
        parser = QueryParser("content", schema=ix.schema)
        query = parser.parse(query_text)
        results = searcher.search(query, limit=20)

        if not results:
            print("No results found.")
            return

        for hit in results:
            doc_id = hit.get("doc_id", "unknown")
            source = hit.get("source_file", "")

            # Try to get a highlighted snippet
            try:
                snippet = hit.highlights("content", top=2)
            except KeyError:
                snippet = ""

            # Fallback if highlighting isn't available
            if not snippet:
                content = hit.get("content", "")
                if content:
                    snippet = (content[:400] + "…") if len(content) > 400 else content
                else:
                    snippet = "[no stored content in index]"

            print(f"📄 {doc_id}  (score={hit.score:.2f})")
            if source:
                print(f"    Source: {source}")
            print("-" * 80)
            print(snippet)
            print()


if __name__ == "__main__":
    main()
