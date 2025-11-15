from __future__ import annotations

from pathlib import Path

from whoosh.fields import Schema, TEXT, ID
from whoosh import index
from whoosh.analysis import StemmingAnalyzer

from vault_core.paths import OCR_DIR, OUTPUT_DIR

INDEX_DIR = OUTPUT_DIR / "index"


def build_schema() -> Schema:
    """
    Schema must match what search_cli.py expects:
    - doc_id: identifier (stem of txt file)
    - source_file: path to the txt file
    - content: full text, stored so we can show snippets/highlights
    """
    return Schema(
        doc_id=ID(stored=True, unique=True),
        source_file=ID(stored=True),
        content=TEXT(stored=True, analyzer=StemmingAnalyzer()),
    )


def ensure_index():
    """
    Ensure an index exists on disk and return an Index object.
    """
    INDEX_DIR.mkdir(parents=True, exist_ok=True)

    if not index.exists_in(INDEX_DIR):
        return index.create_in(INDEX_DIR, build_schema())
    return index.open_dir(INDEX_DIR)


def rebuild_index() -> None:
    """
    Rebuild the entire index from all .txt files in OCR_DIR.
    """
    INDEX_DIR.mkdir(parents=True, exist_ok=True)
    ix = index.create_in(INDEX_DIR, build_schema())
    writer = ix.writer()

    for txt_file in OCR_DIR.glob("*.txt"):
        content = txt_file.read_text(errors="ignore")
        writer.update_document(
            doc_id=txt_file.stem,
            source_file=str(txt_file),
            content=content,
        )
        print(f"Indexed txt file into search index: {txt_file}")

    writer.commit()
    print(f"Full index rebuild complete at {INDEX_DIR}")


def update_index_for_file(txt_path: Path) -> None:
    """
    Update (or add) a single txt file into the index.
    """
    txt_path = Path(txt_path)

    if not txt_path.exists():
        raise FileNotFoundError(f"TXT file does not exist: {txt_path}")

    ix = ensure_index()
    content = txt_path.read_text(errors="ignore")

    writer = ix.writer()
    writer.update_document(
        doc_id=txt_path.stem,
        source_file=str(txt_path),
        content=content,
    )
    writer.commit()
    print(f"Updated index for: {txt_path}")


__all__ = [
    "INDEX_DIR",
    "build_schema",
    "ensure_index",
    "rebuild_index",
    "update_index_for_file",
]
