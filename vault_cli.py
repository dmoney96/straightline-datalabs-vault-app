from __future__ import annotations

import argparse
import json
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

import requests

from vault_core.paths import INPUT_DIR, OUTPUT_DIR
from vault_core.logging_config import get_logger

# Manifest lives here
MANIFEST_PATH = OUTPUT_DIR / "manifest.json"
logger = get_logger("cli")


# -----------------------------
# Manifest helpers
# -----------------------------
def load_manifest() -> Dict[str, Dict[str, Any]]:
    if not MANIFEST_PATH.exists():
        return {}
    try:
        return json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))
    except Exception as e:
        logger.error("Failed to read manifest %s: %r", MANIFEST_PATH, e)
        return {}


def save_manifest(data: Dict[str, Dict[str, Any]]) -> None:
    MANIFEST_PATH.parent.mkdir(parents=True, exist_ok=True)
    MANIFEST_PATH.write_text(
        json.dumps(data, indent=2, sort_keys=True),
        encoding="utf-8",
    )
    logger.info("Manifest updated at %s", MANIFEST_PATH)


def upsert_record(record: Dict[str, Any]) -> None:
    doc_id = record["doc_id"]
    manifest = load_manifest()
    manifest[doc_id] = record
    save_manifest(manifest)


def get_record(doc_id: str) -> Optional[Dict[str, Any]]:
    manifest = load_manifest()
    return manifest.get(doc_id)


# -----------------------------
# Shell helpers
# -----------------------------
def run_py(args: List[str]) -> None:
    """
    Run a Python module/script in this venv, raising on error.
    """
    cmd = [sys.executable] + args
    logger.info("Running: %s", " ".join(cmd))
    subprocess.run(cmd, check=True)


# -----------------------------
# Commands
# -----------------------------
def cmd_ingest_url(args: argparse.Namespace) -> None:
    """
    Download a PDF from URL, save to input/, update manifest with metadata,
    OCR it, and rebuild the index.
    """
    url: str = args.url
    tenant_id: str = args.tenant_id or "default"
    tags: List[str] = args.tags or []
    collection: Optional[str] = args.collection

    logger.info("Ingesting URL: %s (tenant=%s)", url, tenant_id)

    # Download PDF
    INPUT_DIR.mkdir(parents=True, exist_ok=True)
    # Derive filename from URL
    name = url.rstrip("/").split("/")[-1] or "download.pdf"
    if not name.lower().endswith(".pdf"):
        name = name + ".pdf"
    pdf_path = INPUT_DIR / name

    resp = requests.get(url, timeout=60)
    resp.raise_for_status()
    pdf_path.write_bytes(resp.content)
    logger.info("Saved PDF to %s", pdf_path)

    # Doc ID = stem of filename (e.g. p463)
    doc_id = pdf_path.stem

    # Manifest record
    now = datetime.now(timezone.utc).isoformat()
    record: Dict[str, Any] = {
        "doc_id": doc_id,
        "tenant_id": tenant_id,
        "source_file": str(pdf_path),
        "ingest_type": "url",
        "source_url": url,
        "created_at": now,
        "extra": {
            "tags": tags,
            "collection": collection,
        },
    }
    upsert_record(record)
    logger.info("Upserted manifest record for %s", doc_id)

    # OCR & reindex
    run_py(["scripts/extract_text.py", pdf_path.name])
    run_py(["scripts/index_docs.py"])

    print(f"Ingest complete for {doc_id}")
    print(f"  source: {url}")
    print(f"  file:   {pdf_path}")
    print(f"  tags:   {tags}")
    if collection:
        print(f"  collection: {collection}")


def cmd_ocr_file(args: argparse.Namespace) -> None:
    """
    OCR a local PDF in input/ (or a path) and rebuild index.
    """
    pdf_arg: str = args.path
    pdf_path = Path(pdf_arg)
    if not pdf_path.is_absolute():
        pdf_path = INPUT_DIR / pdf_arg

    if not pdf_path.exists():
        raise SystemExit(f"PDF not found: {pdf_path}")

    logger.info("OCR for local PDF: %s", pdf_path)
    run_py(["scripts/extract_text.py", pdf_path.name])
    run_py(["scripts/index_docs.py"])
    print(f"OCR + reindex complete for {pdf_path}")


def cmd_reindex(args: argparse.Namespace) -> None:
    """
    Rebuild the Whoosh index from ocr/ directory.
    """
    logger.info("Rebuilding index")
    run_py(["scripts/index_docs.py"])
    print("Index rebuild complete.")


def cmd_search(args: argparse.Namespace) -> None:
    """
    CLI search wrapper around scripts/search_cli.py.
    """
    terms: List[str] = args.terms
    if not terms:
        raise SystemExit("Please provide search terms")

    run_py(["scripts/search_cli.py"] + terms)


def cmd_list_docs(args: argparse.Namespace) -> None:
    """
    Human-friendly listing of manifest entries.
    """
    manifest = load_manifest()
    docs = sorted(manifest.values(), key=lambda r: r.get("doc_id", ""))

    print(f"{len(docs)} document(s) in manifest:\n")
    for rec in docs:
        doc_id = rec.get("doc_id", "<unknown>")
        ingest_type = rec.get("ingest_type", "unknown")
        tenant_id = rec.get("tenant_id", "default")
        extra = rec.get("extra") or {}
        tags = extra.get("tags") or []
        collection = extra.get("collection")

        print(f"- {doc_id}  [{ingest_type}] tenant={tenant_id}")
        if collection:
            print(f"    collection: {collection}")
        if tags:
            print(f"    tags: {', '.join(tags)}")
        source_file = rec.get("source_file")
        source_url = rec.get("source_url")
        if source_file:
            print(f"    {source_file}")
        if source_url:
            print(f"    {source_url}")


def cmd_show_doc(args: argparse.Namespace) -> None:
    """
    Show raw manifest JSON for a single doc_id.
    """
    doc_id: str = args.doc_id
    rec = get_record(doc_id)
    if not rec:
        raise SystemExit(f"No manifest record for doc_id={doc_id!r}")

    print(f"Manifest record for {doc_id!r}:")
    print(json.dumps(rec, indent=2, sort_keys=True))


def cmd_delete_doc(args: argparse.Namespace) -> None:
    """
    Remove a document from the manifest, optionally delete files, and optionally reindex.
    """
    doc_id: str = args.doc_id
    manifest = load_manifest()

    rec = manifest.pop(doc_id, None)
    if not rec:
        print(f"No manifest record for {doc_id!r}")
        return

    save_manifest(manifest)
    logger.info("Deleted manifest record for %s", doc_id)

    # Delete files unless --keep-files is set
    if not args.keep_files:
        source_file = rec.get("source_file")
        if source_file:
            pdf_path = Path(source_file)
            try:
                pdf_path.unlink()
                logger.info("Deleted source file %s", pdf_path)
            except FileNotFoundError:
                logger.warning("Source file %s was already missing", pdf_path)
            except Exception as e:
                logger.error("Error deleting %s: %r", pdf_path, e)

            # Try to delete OCR text too
            ocr_txt = pdf_path.with_suffix(".txt")
            try:
                ocr_txt.unlink()
                logger.info("Deleted OCR text %s", ocr_txt)
            except FileNotFoundError:
                logger.warning("OCR text %s was already missing", ocr_txt)
            except Exception as e:
                logger.error("Error deleting %s: %r", ocr_txt, e)

    # Reindex unless --no-reindex
    if args.reindex:
        cmd_reindex(args)

    print(f"Deleted {doc_id}")
    if not args.keep_files:
        print("  (source + OCR files removed)")
    if args.reindex:
        print("  (index rebuilt)")


# -----------------------------
# Argparse wiring
# -----------------------------
def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="vault_cli",
        description="StraightLine Data Vault CLI",
    )

    sub = p.add_subparsers(dest="command", required=True)

    # ingest-url
    p_ingest = sub.add_parser(
        "ingest-url",
        help="Ingest a remote PDF by URL (download + manifest + OCR + index)",
    )
    p_ingest.add_argument("url", help="Public PDF URL")
    p_ingest.add_argument(
        "--tenant-id",
        help="Tenant identifier (default: default)",
    )
    p_ingest.add_argument(
        "--tags",
        nargs="*",
        help="Tags to attach (space-separated)",
    )
    p_ingest.add_argument(
        "--collection",
        help="Collection/group name (e.g. 'tax-guides-2025')",
    )
    p_ingest.set_defaults(func=cmd_ingest_url)

    # ocr-file
    p_ocr = sub.add_parser(
        "ocr-file",
        help="OCR a local PDF (in input/ or a full path) and reindex",
    )
    p_ocr.add_argument("path", help="PDF path or name under input/")
    p_ocr.set_defaults(func=cmd_ocr_file)

    # reindex
    p_reindex = sub.add_parser(
        "reindex",
        help="Rebuild the Whoosh index from existing OCR text files",
    )
    p_reindex.set_defaults(func=cmd_reindex)

    # search
    p_search = sub.add_parser(
        "search",
        help="Search via scripts/search_cli.py",
    )
    p_search.add_argument("terms", nargs=argparse.REMAINDER, help="Search terms")
    p_search.set_defaults(func=cmd_search)

    # list-docs
    p_list = sub.add_parser(
        "list-docs",
        help="List all manifest documents and their metadata",
    )
    p_list.set_defaults(func=cmd_list_docs)

    # show-doc
    p_show = sub.add_parser(
        "show-doc",
        help="Show detailed manifest record for a doc_id",
    )
    p_show.add_argument("doc_id", help="Document ID")
    p_show.set_defaults(func=cmd_show_doc)

    # delete-doc
    p_del = sub.add_parser(
        "delete-doc",
        help="Delete a manifest entry, optionally its files, and optionally reindex",
    )
    p_del.add_argument("doc_id", help="Document ID to delete")
    p_del.add_argument(
        "--keep-files",
        action="store_true",
        help="Do NOT delete the source PDF and OCR text",
    )
    p_del.add_argument(
        "--no-reindex",
        dest="reindex",
        action="store_false",
        help="Skip rebuilding the index after deletion",
    )
    p_del.set_defaults(func=cmd_delete_doc, reindex=True)

    return p


def main(argv: Optional[List[str]] = None) -> None:
    parser = build_parser()
    args = parser.parse_args(argv)
    func = getattr(args, "func", None)
    if func is None:
        parser.print_help()
        raise SystemExit(1)
    func(args)


if __name__ == "__main__":
    main()
