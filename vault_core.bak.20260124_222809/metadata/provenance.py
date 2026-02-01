from __future__ import annotations

from dataclasses import dataclass, asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Optional

import json
import uuid

from vault_core.paths import DATA_DIR


# Where individual provenance JSON files will live:
#   data/provenance/<uuid>.json
PROVENANCE_DIR = DATA_DIR / "provenance"
PROVENANCE_DIR.mkdir(parents=True, exist_ok=True)


@dataclass
class ProvenanceRecord:
    """
    Minimal provenance + chain-of-custody record for a single source document.

    This is intentionally small but structured so we can extend later without
    breaking older records.
    """
    id: str                          # internal UUID
    source_path: str                 # absolute path to raw file on disk
    source_url: Optional[str]        # where it was fetched from, if any
    collected_at: str                # ISO 8601 UTC timestamp
    collected_by: str                # "who/what" collected it (tool, user, etc.)
    content_type: str                # e.g. "pdf", "html", "text"
    notes: Optional[str] = None      # freeform notes
    extra: Dict[str, Any] | None = None  # flexible bucket for future fields


def _now_utc_iso() -> str:
    """Return an ISO8601 timestamp in UTC."""
    return datetime.now(timezone.utc).isoformat()


def build_provenance_record(
    source_path: Path,
    *,
    source_url: Optional[str] = None,
    collected_by: str = "straightline-vault/mvp",
    content_type: str = "pdf",
    notes: Optional[str] = None,
    extra: Optional[Dict[str, Any]] = None,
) -> ProvenanceRecord:
    """
    Build a new ProvenanceRecord for a given source file.

    This does NOT write anything to disk; use save_provenance() for that.
    """
    source_path = Path(source_path).resolve()

    return ProvenanceRecord(
        id=uuid.uuid4().hex,
        source_path=str(source_path),
        source_url=source_url,
        collected_at=_now_utc_iso(),
        collected_by=collected_by,
        content_type=content_type,
        notes=notes,
        extra=extra or {},
    )


def save_provenance(record: ProvenanceRecord) -> Path:
    """
    Persist a ProvenanceRecord as JSON under data/provenance/<id>.json
    and return the path.
    """
    out_path = PROVENANCE_DIR / f"{record.id}.json"
    with out_path.open("w", encoding="utf-8") as f:
        json.dump(asdict(record), f, ensure_ascii=False, indent=2)
    return out_path


def record_and_save_provenance(
    source_path: Path,
    *,
    source_url: Optional[str] = None,
    collected_by: str = "straightline-vault/mvp",
    content_type: str = "pdf",
    notes: Optional[str] = None,
    extra: Optional[Dict[str, Any]] = None,
) -> Path:
    """
    Convenience wrapper: build + save in one call.
    Returns the path to the saved JSON.
    """
    rec = build_provenance_record(
        source_path=source_path,
        source_url=source_url,
        collected_by=collected_by,
        content_type=content_type,
        notes=notes,
        extra=extra,
    )
    return save_provenance(rec)


def load_provenance(provenance_id: str) -> Dict[str, Any]:
    """
    Load a provenance JSON file by its UUID-ish ID and return it as a dict.
    """
    path = PROVENANCE_DIR / f"{provenance_id}.json"
    with path.open("r", encoding="utf-8") as f:
        return json.load(f)
