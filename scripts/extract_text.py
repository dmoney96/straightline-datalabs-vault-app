import sys
from pathlib import Path

# Make sure project root is on sys.path
ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from vault_core.paths import INPUT_DIR, OCR_DIR
from vault_core.ocr import pdf_to_text  # now lives in vault_core/ocr/__init__.py


def main():
    if len(sys.argv) != 2:
        print("Usage: python scripts/extract_text.py <pdf-name-or-path>")
        raise SystemExit(1)

    arg = sys.argv[1]

    pdf_path = Path(arg)
    if not pdf_path.is_absolute():
        pdf_path = INPUT_DIR / arg

    if not pdf_path.exists():
        raise FileNotFoundError(f"{pdf_path} does not exist")

    out_txt = OCR_DIR / (pdf_path.stem + ".txt")
    out_txt.parent.mkdir(parents=True, exist_ok=True)

    pdf_to_text(pdf_path, out_txt)
    print(f"OCR complete → {out_txt}")


if __name__ == "__main__":
    main()
