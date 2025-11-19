import argparse
from vault_core.manifest import iter_manifest

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--kind", help="Filter by kind (e.g. court_filing, news_article)")
    parser.add_argument("--limit", type=int, default=20, help="Max rows to show")
    args = parser.parse_args()

    records = list(iter_manifest() or [])
    shown = 0

    for rec in records:
        if args.kind and rec.get("kind") != args.kind:
            continue

        print(
            f"[{rec.get('timestamp')}] {rec.get('kind')}"
            f" | PDF={rec.get('pdf')}"
            f" | TXT={rec.get('txt')}"
            f" | URL={rec.get('source_url')}"
        )
        shown += 1
        if shown >= args.limit:
            break

    if shown == 0:
        print("No records matched.")

if __name__ == "__main__":
    main()
