# app/documents/parsers/pptx_parser.py
#
# Updated: pictures detected via MSO_SHAPE_TYPE.PICTURE are now
# extracted and described via vision LLM, same filtering as PDF/DOCX.

import io
import logging

from langchain_core.documents import Document
from pptx import Presentation
from pptx.enum.shapes import MSO_SHAPE_TYPE

from app.documents.image_description import describe_image
from app.documents.image_filter import reset_seen_images, should_describe_image

logger = logging.getLogger("app.documents.parsers.pptx")


async def parse_pptx(file_bytes: bytes, filename: str) -> list[Document]:
    """One Document per slide."""
    prs = Presentation(io.BytesIO(file_bytes))
    reset_seen_images()
    documents = []

    for i, slide in enumerate(prs.slides):
        text_parts: list[str] = []
        described_count = 0
        skipped_count = 0

        for shape in slide.shapes:
            if shape.has_text_frame and shape.text_frame.text.strip():
                text_parts.append(shape.text_frame.text)
            elif shape.has_table:
                text_parts.append(_table_to_markdown(shape.table))
            elif shape.shape_type == MSO_SHAPE_TYPE.PICTURE:
                try:
                    image_bytes = shape.image.blob
                    width, height = shape.image.size  # pixel dimensions,
                    # confirmed available via python-pptx's Image object
                    if should_describe_image(image_bytes, width, height):
                        description = await describe_image(image_bytes)
                        text_parts.append(f"[Image description: {description}]")
                        described_count += 1
                    else:
                        skipped_count += 1
                except Exception as e:
                    logger.warning(
                        "%s slide %d: image processing failed: %s",
                        filename, i + 1, e,
                    )
                    skipped_count += 1

        if slide.has_notes_slide:
            notes_text = slide.notes_slide.notes_text_frame.text.strip()
            if notes_text:
                text_parts.append(f"[Speaker notes: {notes_text}]")

        if described_count or skipped_count:
            logger.info(
                "%s slide %d: %d image(s) described, %d skipped",
                filename, i + 1, described_count, skipped_count,
            )

        documents.append(
            Document(
                page_content="\n\n".join(text_parts),
                metadata={
                    "source": filename,
                    "file_type": "pptx",
                    "slide": i + 1,
                    "images_described": described_count,
                    "images_skipped": skipped_count,
                },
            )
        )
    return documents


def _table_to_markdown(table) -> str:
    rows = [[cell.text.strip() for cell in row.cells] for row in table.rows]
    if not rows:
        return ""
    header = "| " + " | ".join(rows[0]) + " |"
    separator = "| " + " | ".join("---" for _ in rows[0]) + " |"
    body = "\n".join("| " + " | ".join(row) + " |" for row in rows[1:])
    return "\n".join([header, separator, body])