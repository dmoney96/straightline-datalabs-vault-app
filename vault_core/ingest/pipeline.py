from __future__ import annotations

import logging
from pathlib import Path
from typing import Dict, Optional, Tuple
from urllib.parse import urlparse

from vault_core.paths import INPUT_DIR, OCR_DIR
from vault_core.ocr import pdf_to_text
from vault_core.ingest.fetch import fetch_pdf
from vault_core.manifest import append_manifest_entry
from vault_core.search.indexer import update_index_for_file

log = logging.getLogger(__name__)


def _is_url(s: str) -> bool:
    """Return True if the string looks like an HTTP(S) URL."""
    parsed = urlparse(s)
    return parsed.scheme in ("http", "https")


def build_provenance_record(
    *,
    pdf_path: Path,
    txt_path: Path,
    source_url: Optional[str],
    kind: str,
) -> Dict[str, str]:
    """
    Normalize how we describe one ingested document.

    We store simple string paths so the manifest stays portable / JSON-friendly.
    """
    return {
        "kind": kind,
        "pdf": str(pdf_path),
        "txt": str(txt_path),
        "source_url": source_url,
    }


def ingest_source(source: str) -> Tuple[Path, Path]:
    """
    High-level ingest step:

      - If `source` is a URL:
          * download PDF into input/
      - Else:
          * treat it as a local file name/path (relative to input/ if not absolute)

      - OCR → ocr/<stem>.txt
      - Append to manifest
      - Update search index for that text file
      - Return (pdf_path, txt_path)
    """
    # 1) Decide if it's URL or local
    if _is_url(source):
        log.info("Ingesting URL: %s", source)
        pdf_path = fetch_pdf(source)
        kind = "url_fetch"
        source_url = source
    else:
        log.info("Ingesting local file: %s", source)
        pdf_path = Path(source)
        if not pdf_path.is_absolute():
            pdf_path = INPUT_DIR / pdf_path
        pdf_path = pdf_path.resolve()
        if not pdf_path.exists():
            raise FileNotFoundError(f"{pdf_path} does not exist")
        kind = "local_file"
        source_url = None

    # 2) OCR output path
    txt_path = OCR_DIR / (pdf_path.stem + ".txt")

    # 3) OCR the PDF into text
    pdf_to_text(pdf_path, txt_path)

    # 4) Manifest entry
    prov = build_provenance_record(
        pdf_path=pdf_path,
        txt_path=txt_path,
        source_url=source_url,
        kind=kind,
    )
    append_manifest_entry(prov)

    # 5) Update the search index
    update_index_for_file(txt_path)

    return pdf_path, txt_path


__all__ = ["ingest_source", "build_provenance_record"]
