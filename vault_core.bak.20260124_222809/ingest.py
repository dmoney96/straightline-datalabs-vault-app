from pathlib import Path
from urllib.parse import urlparse

import requests

from .paths import INPUT_DIR


def _guess_filename_from_url(url: str) -> str:
    """
    Take a URL and try to guess a reasonable filename.
    Ensures it ends with .pdf.
    """
    parsed = urlparse(url)
    name = Path(parsed.path).name or "document.pdf"

    if not name.lower().endswith(".pdf"):
        name += ".pdf"

    return name


def download_pdf(url: str) -> Path:
    """
    Download a PDF from the given URL into INPUT_DIR.
    Returns the full path to the saved file.
    """
    INPUT_DIR.mkdir(parents=True, exist_ok=True)

    filename = _guess_filename_from_url(url)
    out_path = INPUT_DIR / filename

    resp = requests.get(url, timeout=60)
    resp.raise_for_status()

    out_path.write_bytes(resp.content)

    return out_path
