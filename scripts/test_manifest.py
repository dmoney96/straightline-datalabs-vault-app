from pathlib import Path
from vault_core.manifest import append_manifest_entry, iter_manifest
from vault_core.paths import DATA_DIR

def main():
    # 1) append a fake entry
    entry = {
        "kind": "test_record",
        "pdf": "pdfs/example.pdf",
        "txt": "txt/example.txt",
        "source_url": "https://example.com/test",
    }
    append_manifest_entry(entry)
    print(f"Wrote test entry to {DATA_DIR / 'manifest.jsonl'}")

    # 2) iterate and show the last few
    print("Last few records:")
    last_five = list(iter_manifest())[-5:]
    for rec in last_five:
        print(rec)

if __name__ == "__main__":
    main()
