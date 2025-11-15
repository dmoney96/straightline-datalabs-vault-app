from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional

from .paths import DATA_DIR


MANIFEST_PATH = DATA_DIR / "manifest.jsonl"


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def make_record(
    doc_id: str,
    source_file: Path | str,
    ingest_type: str,
    source_url: str | None = None,
    *,
    tenant_id: str = "default",
    tags: Optional[List[str]] = None,
    collection: str | None = None,
    extra: Dict[str, Any] | None = None,
) -> Dict[str, Any]:
    """
    Build a normalized manifest record.

    ingest_type: "url", "local", etc.
    tenant_id:   logical owner / namespace (single-tenant for now, but future-proof).
    tags:        lightweight labels (["irs", "lawsuit", ...]).
    collection:  a named grouping (e.g. "tax-guides-2025").
    """
    base_extra: Dict[str, Any] = {}
    if extra:
        base_extra.update(extra)

    if tags is not None:
        base_extra.setdefault("tags", list(tags))
    if collection is not None:
        base_extra.setdefault("collection", collection)

    return {
        "doc_id": doc_id,
        "tenant_id": tenant_id,
        "source_file": str(source_file),
        "ingest_type": ingest_type,
        "source_url": source_url,
        "created_at": _now_iso(),
        "extra": base_extra,
    }


def append_record(record: Dict[str, Any]) -> None:
    """
    Append a single record as JSONL.
    """
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    with MANIFEST_PATH.open("a", encoding="utf-8") as f:
        f.write(json.dumps(record, ensure_ascii=False) + "\n")


def iter_records() -> Iterable[Dict[str, Any]]:
    """
    Iterate over all manifest records. Safe if file doesn't exist.
    """
    if not MANIFEST_PATH.exists():
        return []

    def _gen():
        with MANIFEST_PATH.open("r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    yield json.loads(line)
                except json.JSONDecodeError:
                    # Skip malformed lines rather than blowing up.
                    continue

    return _gen()
