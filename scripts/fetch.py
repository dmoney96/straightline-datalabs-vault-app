import sys, re
from pathlib import Path
from urllib.parse import urlparse
import requests
from paths import INPUT_DIR

def safe_name(url: str) -> str:
    p = urlparse(url)
    name = Path(p.path).name or "download"
    if not re.search(r"\.[A-Za-z0-9]{2,5}$", name):
        name += ".bin"
    return re.sub(r"[^A-Za-z0-9._-]", "_", name)

def main():
    if len(sys.argv) != 2:
        print("Usage: python fetch.py <url>")
        sys.exit(2)
    url = sys.argv[1]
    fname = safe_name(url)
    out = INPUT_DIR / fname
    r = requests.get(url, timeout=60)
    r.raise_for_status()
    out.write_bytes(r.content)
    print(out)

if __name__ == "__main__":
    main()
