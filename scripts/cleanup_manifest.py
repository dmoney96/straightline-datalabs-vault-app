#!/usr/bin/env python
from __future__ import annotations

import argparse
import json
import sys
from collections import defaultdict
from pathlib import Path

# Make sure project root is on sys.path so "vault_core" is importable
ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from vault_core.manifest import iter_manifest, MANIFEST_PATH  # type: ignore[import]


def load_raw_lines(path: Path) -> list[str]:
    if not path.exists():
        return []
    return path.read_text(encoding="utf-8").splitlines()


def parse_entries(lines: list[str]) -> list[dict]:
    entries: list[dict] = []
    for idx, line in enumerate(lines, start=1):
        line = line.strip()
        if not line:
            continue
        try:
            rec = json.loads(line)
        except json.JSONDecodeError:
            print(f"[WARN] Skipping malformed JSON on line {idx}", file=sys.stderr)
            continue
        if not isinstance(rec, dict):
            print(f"[WARN] Non-dict JSON on line {idx}, skipping", file=sys.stderr)
            continue
        entries.append(rec)
    return entries


def compute_dedup_key(rec: dict) -> tuple:
    """
    Build a key that identifies 'the same logical record'.

    We use:
      - kind
      - case
      - pdf
      - txt
      - source_url

    Two entries with identical keys are considered duplicates,
    and only the first one will be kept.
    """
    return (
        rec.get("kind"),
        rec.get("case"),
        rec.get("pdf"),
        rec.get("txt"),
        rec.get("source_url"),
    )


def should_drop_legacy_kind_case_anomaly(rec: dict, pdf_to_cases: dict[str, set[str]]) -> bool:
    """
    Handle the specific historical weirdness you saw:

      kind = "epstein_1320", case = None, same pdf/txt as a proper local_file entry.

    If:
      - case is None
      - kind is not one of the expected 'kind' values
      - and we see another entry with the same PDF but a valid case,
    then we consider this a legacy duplicate/anomaly and drop it.
    """
    case = rec.get("case")
    kind = rec.get("kind")
    pdf = rec.get("pdf")

    if case is not None:
        return False

    # "Normal" kinds we expect
    expected_kinds = {"local_file", "url_fetch", "test_record"}

    if kind in expected_kinds:
        return False

    if not pdf:
        return False

    # If this PDF is already associated with at least one case,
    # and this record has a funky 'kind' that looks like a case name,
    # we treat it as an anomaly.
    cases_for_pdf = pdf_to_cases.get(str(pdf), set())
    if cases_for_pdf:
        return True

    return False


def cleanup_manifest(dry_run: bool = False) -> None:
    raw_lines = load_raw_lines(MANIFEST_PATH)
    if not raw_lines:
        print("[INFO] No manifest.jsonl found or file is empty.")
        return

    entries = parse_entries(raw_lines)
    if not entries:
        print("[INFO] Manifest has no valid JSON entries.")
        return

    print(f"[INFO] Loaded {len(entries)} manifest entries from {MANIFEST_PATH}")

    # Build a mapping from PDF → set(cases) for anomaly detection
    pdf_to_cases: dict[str, set[str]] = defaultdict(set)
    for rec in entries:
        pdf = rec.get("pdf")
        case = rec.get("case")
        if pdf and case:
            pdf_to_cases[str(pdf)].add(str(case))

    seen_keys: set[tuple] = set()
    cleaned: list[dict] = []
    dropped_duplicates = 0
    dropped_anomalies = 0

    for rec in entries:
        key = compute_dedup_key(rec)

        # Legacy anomaly handling (kind=case, case=None)
        if should_drop_legacy_kind_case_anomaly(rec, pdf_to_cases):
            dropped_anomalies += 1
            continue

        if key in seen_keys:
            dropped_duplicates += 1
            continue

        seen_keys.add(key)
        cleaned.append(rec)

    print(f"[INFO] After cleanup: {len(cleaned)} entries")
    print(f"[INFO] Dropped {dropped_duplicates} exact duplicates")
    print(f"[INFO] Dropped {dropped_anomalies} legacy kind/case anomalies")

    if dry_run:
        print("[INFO] Dry run: not writing changes to disk.")
        return

    # Write back to manifest.jsonl
    backup_path = MANIFEST_PATH.with_suffix(".jsonl.bak")
    MANIFEST_PATH.replace(backup_path)
    print(f"[INFO] Backed up original manifest to {backup_path}")

    with MANIFEST_PATH.open("w", encoding="utf-8") as f:
        for rec in cleaned:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")

    print(f"[INFO] Wrote cleaned manifest to {MANIFEST_PATH}")


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Clean up manifest.jsonl (deduplicate & remove legacy anomalies)."
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Show what would be done without modifying manifest.jsonl.",
    )
    args = parser.parse_args()

    cleanup_manifest(dry_run=args.dry_run)


if __name__ == "__main__":
    main()
