from __future__ import annotations

import json
from pathlib import Path
from typing import Dict, Any
from datetime import datetime

from vault_core.paths import DATA_DIR

MANIFEST_PATH = DATA_DIR / "manifest.jsonl"
MANIFEST_PATH.parent.mkdir(parents=True, exist_ok=True)


def append_manifest_entry(entry: Dict[str, Any]) -> None:
    """
    Append a single manifest record.

    `entry` MUST already contain keys:
        - kind
        - pdf
        - txt
        - source_url

    This function simply stamps a timestamp and writes it.
    """
    record = dict(entry)  # shallow copy
    record["timestamp"] = datetime.now().isoformat()

    with MANIFEST_PATH.open("a", encoding="utf-8") as f:
        f.write(json.dumps(record, ensure_ascii=False) + "\n")


def iter_manifest():
    """
    Generator that yields each manifest record as a dict.
    Ignores malformed JSON lines.
    """
    if not MANIFEST_PATH.exists():
        return

    with MANIFEST_PATH.open("r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                yield json.loads(line)
            except Exception:
                continue


__all__ = ["append_manifest_entry", "iter_manifest"]
