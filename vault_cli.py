from __future__ import annotations

import argparse
from pathlib import Path

from whoosh.index import open_dir
from whoosh.qparser import QueryParser
from whoosh import scoring

from vault_core.paths import INPUT_DIR, OCR_DIR, OUTPUT_DIR
from vault_core.logging_config import get_logger
from vault_core.ocr import pdf_to_text
from vault_core.ingest.fetch import fetch_pdf
from vault_core.manifest import make_record, append_record, iter_records
from scripts import index_docs as index_mod


logger = get_logger("cli")
INDEX_DIR = OUTPUT_DIR / "index"


# ------------ Commands ------------

def cmd_ingest_url(args: argparse.Namespace) -> None:
    """
    Fetch a PDF from a URL, OCR it, log manifest, and reindex the corpus.
    """
    url = args.url
    logger.info("Ingesting from URL: %s", url)

    pdf_path = Path(fetch_pdf(url))
    logger.info("Fetched → %s", pdf_path)

    # OCR → ocr/<stem>.txt
    txt_path = OCR_DIR / (pdf_path.stem + ".txt")
    logger.info("Running OCR → %s", txt_path)
    pdf_to_text(pdf_path, txt_path)

    # Manifest entry
    rec = make_record(
        doc_id=pdf_path.stem,
        source_file=pdf_path,
        ingest_type="url",
        source_url=url,
    )
    append_record(rec)

    # Rebuild index
    logger.info("Rebuilding index after ingest")
    index_mod.create_index()

    print(f"✅ Ingest complete for {url}")
    print(f"   PDF:     {pdf_path}")
    print(f"   Text:    {txt_path}")
    print(f"   Index:   {INDEX_DIR}")


def cmd_ocr_file(args: argparse.Namespace) -> None:
    """
    OCR a local PDF in input/ into ocr/, log manifest, and reindex.
    """
    name_or_path = args.path
    pdf_path = Path(name_or_path)

    if not pdf_path.is_absolute():
        pdf_path = INPUT_DIR / name_or_path

    if not pdf_path.exists():
        raise FileNotFoundError(f"{pdf_path} does not exist")

    txt_path = OCR_DIR / (pdf_path.stem + ".txt")
    logger.info("OCR local PDF %s → %s", pdf_path, txt_path)
    pdf_to_text(pdf_path, txt_path)

    # Manifest entry
    rec = make_record(
        doc_id=pdf_path.stem,
        source_file=pdf_path,
        ingest_type="local",
        source_url=None,
    )
    append_record(rec)

    # Rebuild index
    logger.info("Rebuilding index after OCR")
    index_mod.create_index()

    print(f"✅ OCR complete for {pdf_path}")
    print(f"   Text:    {txt_path}")
    print(f"   Index:   {INDEX_DIR}")


def cmd_reindex(args: argparse.Namespace) -> None:
    """
    Rebuild the Whoosh index from all files in ocr/.
    """
    logger.info("Manual reindex requested")
    index_mod.create_index()
    print(f"✅ Index rebuilt at {INDEX_DIR}")


def cmd_search(args: argparse.Namespace) -> None:
    """
    Search the local index from the CLI.
    """
    query_text = " ".join(args.terms).strip()
    if not query_text:
        print("Please provide search terms.")
        return

    if not INDEX_DIR.exists():
        raise SystemExit(f"Index directory {INDEX_DIR} does not exist. "
                         "Run `reindex` or `ingest-url` first.")

    logger.info("CLI search: %r", query_text)

    ix = open_dir(INDEX_DIR)
    parser = QueryParser("content", schema=ix.schema)

    with ix.searcher(weighting=scoring.BM25F()) as searcher:
        q = parser.parse(query_text)
        results = searcher.search(q, limit=args.limit)

        print(f"🔎 Query: {query_text!r}")
        print(f"   Hits: {len(results)}\n")

        if not results:
            return

        for hit in results:
            doc_id = hit.get("doc_id", "<unknown>")
            source = hit.get("source_file", "<unknown>")

            print(f"📄 {doc_id}  (score={hit.score:.2f})")
            print(f"    Source: {source}")
            print("-" * 80)
            snippet = hit.highlights("content", top=2) or hit.get("content", "")[:240]
            print(snippet)
            print()


def cmd_list_docs(args: argparse.Namespace) -> None:
    """
    List all documents known to the manifest.
    """
    recs = list(iter_records())
    if not recs:
        print("No documents in manifest yet.")
        return

    print(f"{len(recs)} document(s) in manifest:\n")
    for rec in recs:
        doc_id = rec.get("doc_id", "<unknown>")
        ingest_type = rec.get("ingest_type", "?")
        source_url = rec.get("source_url")
        source_file = rec.get("source_file")

        src_display = source_url or source_file
        print(f"- {doc_id}  [{ingest_type}]  {src_display}")


def cmd_show_doc(args: argparse.Namespace) -> None:
    """
    Show full manifest record for a single doc_id.
    """
    target = args.doc_id
    for rec in iter_records():
        if rec.get("doc_id") == target:
            print(f"Manifest record for {target!r}:")
            # pretty-print
            import json
            print(json.dumps(rec, indent=2, ensure_ascii=False))
            return

    print(f"No manifest record found for doc_id={target!r}")


# ------------ Argparser wiring ------------

def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="vault_cli",
        description="StraightLine DataLabs Vault – ingest + search CLI",
    )

    sub = p.add_subparsers(dest="command", required=True)

    # ingest-url
    p_ing_url = sub.add_parser(
        "ingest-url",
        help="Fetch a PDF from a URL, OCR it, log manifest, and reindex",
    )
    p_ing_url.add_argument("url", help="Public PDF URL")
    p_ing_url.set_defaults(func=cmd_ingest_url)

    # ocr-file
    p_ocr = sub.add_parser(
        "ocr-file",
        help="OCR a local PDF in input/ and reindex",
    )
    p_ocr.add_argument("path", help="PDF name or path (relative to input/ if not absolute)")
    p_ocr.set_defaults(func=cmd_ocr_file)

    # reindex
    p_idx = sub.add_parser(
        "reindex",
        help="Rebuild the index from existing OCR text files",
    )
    p_idx.set_defaults(func=cmd_reindex)

    # search
    p_search = sub.add_parser(
        "search",
        help="Search the local index from the CLI",
    )
    p_search.add_argument("terms", nargs="+", help="Search terms")
    p_search.add_argument(
        "--limit",
        type=int,
        default=10,
        help="Max results to show (default: 10)",
    )
    p_search.set_defaults(func=cmd_search)

    # list-docs
    p_list = sub.add_parser(
        "list-docs",
        help="List documents known to the manifest",
    )
    p_list.set_defaults(func=cmd_list_docs)

    # show-doc
    p_show = sub.add_parser(
        "show-doc",
        help="Show manifest metadata for a single document",
    )
    p_show.add_argument("doc_id", help="Document ID (usually the PDF stem)")
    p_show.set_defaults(func=cmd_show_doc)

    return p


def main(argv: list[str] | None = None) -> None:
    parser = build_parser()
    args = parser.parse_args(argv)
    args.func(args)


if __name__ == "__main__":
    main()
