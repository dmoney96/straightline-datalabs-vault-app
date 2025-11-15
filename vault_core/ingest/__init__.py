from __future__ import annotations

from pathlib import Path
from urllib.parse import urlparse

import requests

from vault_core.paths import INPUT_DIR
from vault_core.logging_config import get_logger


logger = get_logger("ingest")


def fetch_pdf(url: str, dest: Path | None = None, timeout: int = 60) -> Path:
    """
    Download a PDF from `url` into INPUT_DIR (or to `dest` if provided).

    Returns the local Path to the downloaded file.
    """
    logger.info("Starting fetch for URL: %s", url)

    if dest is None:
        parsed = urlparse(url)
        filename = Path(parsed.path).name or "download.pdf"
        dest = INPUT_DIR / filename

    dest.parent.mkdir(parents=True, exist_ok=True)

    resp = requests.get(url, timeout=timeout, stream=True)
    resp.raise_for_status()

    with dest.open("wb") as f:
        for chunk in resp.iter_content(chunk_size=8192):
            if not chunk:
                continue
            f.write(chunk)

    logger.info("Wrote %d bytes to %s", dest.stat().st_size, dest)
    return dest
