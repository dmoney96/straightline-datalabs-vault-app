from pathlib import Path

from pdf2image import convert_from_path
import pytesseract

from .paths import INPUT_DIR, OCR_DIR


def resolve_pdf_path(pdf: Path | str) -> Path:
    """
    Resolve a PDF path:
    - If it's absolute, use it as-is.
    - If it's relative, assume it's under INPUT_DIR.
    """
    pdf = Path(pdf)

    if not pdf.is_absolute():
        pdf = INPUT_DIR / pdf

    return pdf


def pdf_to_text(pdf: Path | str, overwrite: bool = False) -> Path:
    """
    Run OCR on a single PDF and write a .txt file into OCR_DIR.
    Returns the path to the .txt file.

    - pdf: filename (relative) or full Path
    - overwrite: if False and txt exists, we skip re-OCR and just return it
    """
    pdf_path = resolve_pdf_path(pdf)

    if not pdf_path.exists():
        raise FileNotFoundError(f"{pdf_path} does not exist")

    OCR_DIR.mkdir(parents=True, exist_ok=True)

    out_txt = OCR_DIR / (pdf_path.stem + ".txt")
    if out_txt.exists() and not overwrite:
        return out_txt

    pages = convert_from_path(str(pdf_path))
    chunks: list[str] = []

    for i, page in enumerate(pages, start=1):
        text = pytesseract.image_to_string(page)
        chunks.append(f"PAGE {i} =====\n{text}\n")

    out_txt.write_text("\n".join(chunks), errors="ignore")
    return out_txt


def batch_ocr(overwrite: bool = False) -> list[Path]:
    """
    OCR all PDFs in INPUT_DIR.
    Returns the list of generated/used txt paths.
    """
    OCR_DIR.mkdir(parents=True, exist_ok=True)
    results: list[Path] = []

    for pdf in INPUT_DIR.glob("*.pdf"):
        txt_path = pdf_to_text(pdf, overwrite=overwrite)
        results.append(txt_path)

    return results
