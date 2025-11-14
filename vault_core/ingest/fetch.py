from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

import requests

from vault_core.paths import INPUT_DIR


def _sha256(path: Path) -> str:
    """Return SHA256 hash of a file."""
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(8192), b""):
            h.update(chunk)
    return h.hexdigest()


def fetch_pdf(url: str, *, filename: Optional[str] = None) -> Path:
    """
    Download a PDF into INPUT_DIR and write a metadata sidecar file.

    Returns the Path to the downloaded PDF.
    """
    INPUT_DIR.mkdir(parents=True, exist_ok=True)

    # Derive filename from URL if none provided
    if filename is None:
        cleaned = url.rstrip("/")
        filename = cleaned.split("/")[-1] or "download.pdf"

    # Make sure it ends in .pdf so tools behave
    if not filename.lower().endswith(".pdf"):
        filename += ".pdf"

    out_path = INPUT_DIR / filename

    # Download
    resp = requests.get(url, timeout=60)
    resp.raise_for_status()
    out_path.write_bytes(resp.content)

    # Build metadata
    meta = {
        "source_url": url,
        "stored_filename": out_path.name,
        "downloaded_at": datetime.now(timezone.utc).isoformat(),
        "sha256": _sha256(out_path),
        "content_type": resp.headers.get("Content-Type"),
        "size_bytes": out_path.stat().st_size,
    }

    # Sidecar: e.g. p463.pdf.meta.json
    meta_path = out_path.with_suffix(out_path.suffix + ".meta.json")
    meta_path.write_text(json.dumps(meta, indent=2), encoding="utf-8")

    return out_path
