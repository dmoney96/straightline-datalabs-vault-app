from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable

from vault_core.paths import DATA_DIR

# Single JSONL manifest file:
# each line = one JSON object describing a source (pdf/txt/provenance/etc.)
MANIFEST_PATH = DATA_DIR / "manifest.jsonl"


def append_manifest_entry(record: Dict[str, Any]) -> None:
    """
    Append a single record to the manifest JSONL file.

    The ingest pipeline passes in something like:
        {
            "pdf": "...",
            "txt": "...",
            "provenance": "...",
            "source_url": "https://..." or None,
        }

    We add a UTC timestamp and write it as one line of JSON.
    """
    entry: Dict[str, Any] = dict(record)
    entry["timestamp"] = datetime.now(timezone.utc).isoformat()

    MANIFEST_PATH.parent.mkdir(parents=True, exist_ok=True)
    with MANIFEST_PATH.open("a", encoding="utf-8") as f:
        json.dump(entry, f, ensure_ascii=False)
        f.write("\n")


def iter_manifest() -> Iterable[Dict[str, Any]]:
    """
    Iterate over all manifest entries as dicts.

    This will be handy later for building:
      - a UI that browses ingested documents
      - audits of who ingested what and when
    """
    if not MANIFEST_PATH.exists():
        # Return an empty iterable
        return []

    def _gen() -> Iterable[Dict[str, Any]]:
        with MANIFEST_PATH.open("r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    yield json.loads(line)
                except json.JSONDecodeError:
                    # Skip malformed lines instead of exploding
                    continue

    return _gen()


__all__ = [
    "MANIFEST_PATH",
    "append_manifest_entry",
    "iter_manifest",
]
