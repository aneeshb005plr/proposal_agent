# app/documents/parsers/docx_parser.py
#
# DOCX extraction via python-docx. Walks paragraphs and tables in
# original document order using iter_inner_content(), which returns
# actual Paragraph/Table objects directly (not raw XML elements) —
# verified against current python-docx docs. Heading levels are
# preserved as markdown '#' prefixes, tables converted to markdown
# syntax. Embedded images are counted, not extracted.

import io
import logging

from docx import Document as DocxDocument
from docx.table import Table
from docx.text.paragraph import Paragraph
from langchain_core.documents import Document

logger = logging.getLogger("app.documents.parsers.docx")


def parse_docx(file_bytes: bytes, filename: str) -> list[Document]:
    """One Document for the whole file."""
    doc = DocxDocument(io.BytesIO(file_bytes))
    parts: list[str] = []

    image_count = sum(
        1 for rel in doc.part.rels.values() if "image" in rel.reltype
    )

    for block in doc.iter_inner_content():
        if isinstance(block, Paragraph):
            if not block.text.strip():
                continue
            if block.style.name.startswith("Heading"):
                level = block.style.name.replace("Heading ", "").strip()
                level = int(level) if level.isdigit() else 1
                parts.append(f"{'#' * level} {block.text}")
            else:
                parts.append(block.text)
        elif isinstance(block, Table):
            parts.append(_table_to_markdown(block))

    if image_count:
        logger.info(
            "%s: %d image(s) detected and skipped", filename, image_count
        )

    return [
        Document(
            page_content="\n\n".join(parts),
            metadata={
                "source": filename,
                "file_type": "docx",
                "images_skipped": image_count,
            },
        )
    ]


def _table_to_markdown(table: Table) -> str:
    rows = [[cell.text.strip() for cell in row.cells] for row in table.rows]
    if not rows:
        return ""
    header = "| " + " | ".join(rows[0]) + " |"
    separator = "| " + " | ".join("---" for _ in rows[0]) + " |"
    body = "\n".join("| " + " | ".join(row) + " |" for row in rows[1:])
    return "\n".join([header, separator, body])