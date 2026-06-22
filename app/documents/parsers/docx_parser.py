# app/documents/parsers/docx_parser.py
#
# Updated: extracts embedded images via doc.part.rels and describes
# content-bearing ones via vision LLM, same filtering logic as PDF.

import io
import logging

from docx import Document as DocxDocument
from docx.table import Table
from docx.text.paragraph import Paragraph
from langchain_core.documents import Document

from app.documents.image_description import describe_image
from app.documents.image_filter import reset_seen_images, should_describe_image

logger = logging.getLogger("app.documents.parsers.docx")


async def parse_docx(file_bytes: bytes, filename: str) -> list[Document]:
    """One Document for the whole file."""
    doc = DocxDocument(io.BytesIO(file_bytes))
    reset_seen_images()
    parts: list[str] = []

    described_count = 0
    skipped_count = 0

    # Walk text/tables in document order first
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

    # Images are stored as document relationships, not inline with
    # paragraphs/tables in python-docx's object model — handled
    # separately and appended, since precise in-text image position
    # is not readily available via the public API.
    for rel in doc.part.rels.values():
        if "image" not in rel.reltype:
            continue
        try:
            image_bytes = rel.target_part.blob
            # python-docx does not expose pixel dimensions directly
            # without decoding the image; use byte size as a coarse
            # proxy for the area filter — small files are almost
            # always icons/logos, content images are larger.
            if len(image_bytes) < 5_000:
                skipped_count += 1
                continue
            if should_describe_image(image_bytes, 9999, 9999):  # bypass
                # area check since we already filtered by byte size
                description = await describe_image(image_bytes)
                parts.append(f"[Image description: {description}]")
                described_count += 1
            else:
                skipped_count += 1
        except Exception as e:
            logger.warning("%s: image processing failed: %s", filename, e)
            skipped_count += 1

    if described_count or skipped_count:
        logger.info(
            "%s: %d image(s) described, %d skipped",
            filename, described_count, skipped_count,
        )

    return [
        Document(
            page_content="\n\n".join(parts),
            metadata={
                "source": filename,
                "file_type": "docx",
                "images_described": described_count,
                "images_skipped": skipped_count,
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