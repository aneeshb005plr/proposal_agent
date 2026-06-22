# app/documents/parsers/docx_parser.py
#
# DOCX extraction via python-docx. Walks paragraphs and tables in
# original document order using iter_inner_content(), which returns
# actual Paragraph/Table objects directly (verified against current
# python-docx 1.2.0 docs — not raw XML elements needing manual
# matching against internal attributes).
#
# Content-bearing images (charts, diagrams, screenshots) are
# described via vision LLM and appended to the document text;
# decorative/repeated images (logos, icons) are filtered out before
# any LLM call.
#
# CORRECTED: pixel dimensions are now obtained via Pillow (real
# decode of the image blob), replacing an earlier byte-size
# approximation. python-docx's image relationships expose only raw
# bytes (rel.target_part.blob) — there is no documented dimension
# property on that object, so Pillow is the correct, verified way to
# get real width/height, exactly as established for PPTX.

import logging
from io import BytesIO

from docx import Document as DocxDocument
from docx.table import Table
from docx.text.paragraph import Paragraph
from langchain_core.documents import Document
from PIL import Image as PILImage

from app.documents.image_description import describe_image
from app.documents.image_filter import reset_seen_images, should_describe_image

logger = logging.getLogger("app.documents.parsers.docx")


async def parse_docx(file_bytes: bytes, filename: str) -> list[Document]:
    """One Document for the whole file."""
    doc = DocxDocument(BytesIO(file_bytes))
    reset_seen_images()
    parts: list[str] = []

    # Walk text/tables in document order
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
    # separately and appended at the end. Precise in-text position
    # is not readily available via the public API, so descriptions
    # cannot be interleaved at their original location, only appended.
    described_count = 0
    skipped_count = 0
    for rel in doc.part.rels.values():
        if "image" not in rel.reltype:
            continue

        description_text, was_described = await _process_image_relationship(
            rel, filename
        )
        if was_described:
            parts.append(description_text)
            described_count += 1
        else:
            skipped_count += 1

    if described_count or skipped_count:
        logger.info(
            "%s: %d image(s) described, %d skipped "
            "(decorative/repeated/failed)",
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


async def _process_image_relationship(rel, filename: str) -> tuple[str, bool]:
    """
    Extracts an image relationship's bytes, determines real pixel
    dimensions via Pillow, decides whether it's content-bearing,
    and if so describes it via vision LLM.

    Returns (description_text, was_described). If was_described is
    False, description_text is an empty string and the caller should
    not append it — this keeps the function a pure computation with
    no side effects on shared state, avoiding the kind of leaked
    module-level state that would break concurrent document processing.
    """
    try:
        image_bytes = rel.target_part.blob

        with PILImage.open(BytesIO(image_bytes)) as pil_img:
            width, height = pil_img.size

        if should_describe_image(image_bytes, width, height):
            description = await describe_image(image_bytes)
            return f"[Image description: {description}]", True
        else:
            return "", False

    except Exception as e:
        logger.warning("%s: image processing failed: %s", filename, e)
        return "", False


def _table_to_markdown(table: Table) -> str:
    rows = [[cell.text.strip() for cell in row.cells] for row in table.rows]
    if not rows:
        return ""
    header = "| " + " | ".join(rows[0]) + " |"
    separator = "| " + " | ".join("---" for _ in rows[0]) + " |"
    body = "\n".join("| " + " | ".join(row) + " |" for row in rows[1:])
    return "\n".join([header, separator, body])