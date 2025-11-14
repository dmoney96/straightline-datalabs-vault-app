from __future__ import annotations

import argparse
import sys
from pathlib import Path

from whoosh.index import open_dir
from whoosh.qparser import QueryParser
from whoosh import scoring

# Make sure project root is on sys.path
ROOT = Path(__file__).resolve().parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from vault_core.paths import INPUT_DIR, OCR_DIR, OUTPUT_DIR  # noqa: E402
from vault_core.logging_config import get_logger            # noqa: E402
from vault_core.ingest import fetch_pdf                     # noqa: E402
from vault_core.ocr import pdf_to_text                      # noqa: E402

logger = get_logger("cli")
INDEX_DIR = OUTPUT_DIR / "index"


# -------- subcommand handlers --------

def cmd_fetch(args: argparse.Namespace) -> None:
    """
    Fetch a PDF by URL into input/.
    """
    url = args.url
    logger.info("CLI fetch: %s", url)
    out_path = fetch_pdf(url)
    print(f"Downloaded → {out_path}")


def cmd_ocr(args: argparse.Namespace) -> None:
    """
    Run OCR on a single PDF in input/ and write text to ocr/.
    """
    pdf_arg = args.pdf
    pdf_path = Path(pdf_arg)
    if not pdf_path.is_absolute():
        pdf_path = INPUT_DIR / pdf_arg

    if not pdf_path.exists():
        raise SystemExit(f"PDF not found: {pdf_path}")

    out_txt = OCR_DIR / (pdf_path.stem + ".txt")
    out_txt.parent.mkdir(parents=True, exist_ok=True)

    logger.info("CLI ocr: %s -> %s", pdf_path, out_txt)
    pdf_to_text(pdf_path, out_txt)
    print(f"OCR complete → {out_txt}")


def cmd_index(args: argparse.Namespace) -> None:
    """
    Rebuild the Whoosh index from all .txt files in ocr/.
    """
    logger.info("CLI index: rebuilding index at %s", INDEX_DIR)
    # Reuse the script function instead of duplicating code.
    from scripts.index_docs import create_index  # type: ignore

    create_index()
    print(f"Index stored in {INDEX_DIR}")


def cmd_search(args: argparse.Namespace) -> None:
    """
    Search the index and print ranked results with snippets.
    """
    query_text = " ".join(args.terms)
    logger.info("CLI search: %r", query_text)

    if not INDEX_DIR.exists():
        raise SystemExit(f"Index directory does not exist: {INDEX_DIR}")

    from textwrap import fill

    try:
        ix = open_dir(INDEX_DIR)
    except Exception as e:  # noqa: BLE001
        logger.exception("Failed to open index: %s", e)
        raise SystemExit("Failed to open index; did you run 'index' first?") from e

    with ix.searcher(weighting=scoring.BM25F()) as searcher:
        qp = QueryParser("content", schema=ix.schema)
        query = qp.parse(query_text)
        results = searcher.search(query, limit=args.limit)

        if not results:
            print(f"No results for query: {query_text!r}")
            return

        print(f"🔎 Search: {query_text!r}\n")
        for hit in results:
            doc_id = hit["doc_id"]
            src = hit["source_file"]
            score = float(hit.score)
            snippet = (
                hit.highlights("content", top=2)
                or hit.get("content", "")[:200]
            )

            print(f"📄 {doc_id}  (score={score:.2f})")
            print(f"    Source: {src}")
            print("-" * 80)
            print(fill(snippet, width=78))
            print()


# -------- argparse wiring --------

def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="vault",
        description="StraightLine DataLabs Vault CLI",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    # fetch
    p_fetch = sub.add_parser("fetch", help="Fetch a PDF URL into input/")
    p_fetch.add_argument("url", help="HTTP(S) URL of the PDF")
    p_fetch.set_defaults(func=cmd_fetch)

    # ocr
    p_ocr = sub.add_parser("ocr", help="Run OCR on a PDF in input/")
    p_ocr.add_argument("pdf", help="PDF filename or path (relative to input/ by default)")
    p_ocr.set_defaults(func=cmd_ocr)

    # index
    p_index = sub.add_parser("index", help="Rebuild search index from ocr/*.txt")
    p_index.set_defaults(func=cmd_index)

    # search
    p_search = sub.add_parser("search", help="Search the index")
    p_search.add_argument("terms", nargs="+", help="Search terms")
    p_search.add_argument(
        "--limit",
        type=int,
        default=10,
        help="Max number of results (default: 10)",
    )
    p_search.set_defaults(func=cmd_search)

    return parser


def main(argv: list[str] | None = None) -> None:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        args.func(args)
    except KeyboardInterrupt:
        print("\nInterrupted.", file=sys.stderr)
        raise SystemExit(130)


if __name__ == "__main__":
    main()
