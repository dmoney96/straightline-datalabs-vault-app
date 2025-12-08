#!/usr/bin/env python
from __future__ import annotations

import sys
from pathlib import Path

# --- Ensure project root is on sys.path ---
ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

# --- Now imports from vault_core will work ---
from vault_core.manifest import iter_manifest
from vault_core.search_backend import index_txt_document


def main() -> int:
    count = 0
    for rec in iter_manifest() or []:
        txt = rec.get("txt")
        if not txt:
            continue

        p = Path(txt)

        try:
            # Accept both absolute and DATA_DIR-relative manifest paths
            index_txt_document(str(p))
            count += 1
        except Exception as e:
            print(f"ERROR indexing {txt}: {e!r}", file=sys.stderr)

    print(f"Reindexed {count} manifest records.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
