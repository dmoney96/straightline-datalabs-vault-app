# scripts/index_docs.py

from pathlib import Path
from whoosh.fields import Schema, TEXT, ID
from whoosh import index
from whoosh.analysis import StemmingAnalyzer
import shutil

from paths import OCR_DIR, OUTPUT_DIR

INDEX_DIR = OUTPUT_DIR / "index"


def build_schema():
    # Store doc_id + source_file + content so we can show snippets later
    return Schema(
        doc_id=ID(stored=True, unique=True),
        source_file=ID(stored=True),
        content=TEXT(stored=True, analyzer=StemmingAnalyzer()),
    )


def create_index():
    # Clear existing index
    if INDEX_DIR.exists():
        shutil.rmtree(INDEX_DIR)
    INDEX_DIR.mkdir(parents=True, exist_ok=True)

    ix = index.create_in(INDEX_DIR, build_schema())
    writer = ix.writer()

    for txt_file in OCR_DIR.glob("*.txt"):
        doc_id = txt_file.stem
        content = txt_file.read_text(errors="ignore")

        writer.add_document(
            doc_id=doc_id,
            source_file=str(txt_file),
            content=content,
        )
        print(f"Indexed: {txt_file}")

    writer.commit()
    print(f"Index stored in {INDEX_DIR}")


if __name__ == "__main__":
    create_index()
