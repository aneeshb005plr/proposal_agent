# app/documents/parsers/pdf_parser.py
#
# PDF extraction via pymupdf4llm + pymupdf. Tables are converted to
# markdown automatically by pymupdf4llm — no LLM call needed. Images
# present on a page are counted via the lower-level pymupdf.Document
# API (page.get_images()), since to_markdown()'s page_chunks output
# does not reliably expose a per-page image count by default.
#
# Verified against pymupdf4llm/pymupdf current docs: to_markdown()
# requires either a file path string or a pymupdf.Document object —
# NOT a raw io.BytesIO passed directly. We open the bytes via
# pymupdf.open(stream=..., filetype="pdf") first.

import logging

import pymupdf
import pymupdf4llm
from langchain_core.documents import Document

logger = logging.getLogger("app.documents.parsers.pdf")


def parse_pdf(file_bytes: bytes, filename: str) -> list[Document]:
    """One Document per page."""
    pdf_doc = pymupdf.open(stream=file_bytes, filetype="pdf")

    md_pages = pymupdf4llm.to_markdown(pdf_doc, page_chunks=True)

    documents = []
    for i, page_dict in enumerate(md_pages):
        image_count = len(pdf_doc[i].get_images())
        if image_count:
            logger.info(
                "%s page %d: %d image(s) detected and skipped "
                "(not extracted — see parser module docs)",
                filename, i + 1, image_count,
            )

        documents.append(
            Document(
                page_content=page_dict["text"],
                metadata={
                    "source": filename,
                    "file_type": "pdf",
                    "page": i + 1,
                    "images_skipped": image_count,
                },
            )
        )

    pdf_doc.close()
    return documents