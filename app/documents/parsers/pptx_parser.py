# app/documents/parsers/pptx_parser.py
#
# PPTX extraction via python-pptx. One Document per slide, including
# speaker notes. Tables converted to markdown. Images counted, not
# extracted.

import io
import logging

from langchain_core.documents import Document
from pptx import Presentation
from pptx.enum.shapes import MSO_SHAPE_TYPE

logger = logging.getLogger("app.documents.parsers.pptx")


def parse_pptx(file_bytes: bytes, filename: str) -> list[Document]:
    """One Document per slide."""
    prs = Presentation(io.BytesIO(file_bytes))
    documents = []

    for i, slide in enumerate(prs.slides):
        text_parts: list[str] = []
        image_count = 0

        for shape in slide.shapes:
            if shape.has_text_frame and shape.text_frame.text.strip():
                text_parts.append(shape.text_frame.text)
            elif shape.has_table:
                text_parts.append(_table_to_markdown(shape.table))
            elif shape.shape_type == MSO_SHAPE_TYPE.PICTURE:
                image_count += 1

        if slide.has_notes_slide:
            notes_text = slide.notes_slide.notes_text_frame.text.strip()
            if notes_text:
                text_parts.append(f"[Speaker notes: {notes_text}]")

        if image_count:
            logger.info(
                "%s slide %d: %d image(s) detected and skipped",
                filename, i + 1, image_count,
            )

        documents.append(
            Document(
                page_content="\n\n".join(text_parts),
                metadata={
                    "source": filename,
                    "file_type": "pptx",
                    "slide": i + 1,
                    "images_skipped": image_count,
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