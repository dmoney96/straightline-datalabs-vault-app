import sys
from pathlib import Path

# Make sure project root is on sys.path so "vault_core" is importable
ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from vault_core.ingest.pipeline import ingest_source
from vault_core.manifest import iter_manifest


def main() -> None:
    if len(sys.argv) != 2:
        print("Usage: python scripts/ingest_url.py <url-or-path>")
        raise SystemExit(1)

    source = sys.argv[1]
    pdf_path, txt_path = ingest_source(source)

    print("Ingested:")
    print(f"  pdf: {pdf_path}")
    print(f"  txt: {txt_path}")

    # Show a tiny tail of the manifest so you can see what got recorded
    entries = list(iter_manifest())
    print("\nLast few manifest entries:")
    for rec in entries[-3:]:
        kind = rec.get("kind")
        url = rec.get("source_url")
        pdf = rec.get("pdf")
        txt = rec.get("txt")
        print(f"  - {kind} {url} -> {pdf} / {txt}")


if __name__ == "__main__":
    main()
