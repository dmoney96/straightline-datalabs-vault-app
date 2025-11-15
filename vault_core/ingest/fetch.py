import requests
from pathlib import Path

from vault_core.paths import INPUT_DIR
from vault_core.logging_config import get_logger

logger = get_logger("fetch")


def fetch_pdf(url: str) -> Path:
    """
    Download a PDF from the internet into the input/ directory.
    Auto-names files based on URL basename.
    """
    logger.info(f"Downloading: {url}")

    r = requests.get(url, timeout=60)
    r.raise_for_status()

    INPUT_DIR.mkdir(parents=True, exist_ok=True)

    filename = url.split("/")[-1]
    out_path = INPUT_DIR / filename

    with open(out_path, "wb") as f:
        f.write(r.content)

    logger.info(f"Saved → {out_path}")
    return out_path
