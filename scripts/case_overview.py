#!/usr/bin/env python3
from __future__ import annotations

from pathlib import Path
from collections import defaultdict

# Ensure vault_core is importable
import sys
ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from vault_core.manifest import iter_manifest


def main():
    entries = list(iter_manifest())
    if not entries:
        print("No manifest entries found.")
        return

    # Summary structure:
    # cases[case_name]["total"] = count
    # cases[case_name]["kinds"][kind] = count
    cases = defaultdict(lambda: {"total": 0, "kinds": defaultdict(int)})

    for rec in entries:
        case = rec.get("case") or rec.get("kind") or "uncategorized"
        kind = rec.get("kind") or "unknown"

        cases[case]["total"] += 1
        cases[case]["kinds"][kind] += 1

    # Display results
    print("\n=== Case Overview ===\n")
    for case, stats in sorted(cases.items()):
        print(f"Case: {case}")
        print(f"  Total documents: {stats['total']}")
        print("  Kind breakdown:")
        for kind, kcount in stats["kinds"].items():
            print(f"    - {kind}: {kcount}")
        print()


if __name__ == "__main__":
    main()
