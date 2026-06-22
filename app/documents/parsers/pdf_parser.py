# app/documents/parsers/pdf_parser.py
#
# Updated: images are now extracted (not just counted) and, when
# they pass the content-image filter, described via vision LLM and
# inserted into page_content so their information becomes searchable.

import logging

import pymupdf
import pymupdf4llm
from langchain_core.documents import Document

from app.documents.image_description import describe_image
from app.documents.image_filter import reset_seen_images, should_describe_image

logger = logging.getLogger("app.documents.parsers.pdf")


async def parse_pdf(file_bytes: bytes, filename: str) -> list[Document]:
    """One Document per page. Content-bearing images are described
    via vision LLM and appended to the page's text."""
    pdf_doc = pymupdf.open(stream=file_bytes, filetype="pdf")
    reset_seen_images()

    md_pages = pymupdf4llm.to_markdown(pdf_doc, page_chunks=True)

    documents = []
    for i, page_dict in enumerate(md_pages):
        page = pdf_doc[i]
        image_list = page.get_images()
        page_text = page_dict["text"]
        described_count = 0
        skipped_count = 0

        for img in image_list:
            xref = img[0]
            try:
                pix = pymupdf.Pixmap(pdf_doc, xref)
                if pix.n - pix.alpha > 3:
                    pix = pymupdf.Pixmap(pymupdf.csRGB, pix)
                image_bytes = pix.tobytes("png")

                if should_describe_image(image_bytes, pix.width, pix.height):
                    description = await describe_image(image_bytes)
                    page_text += f"\n\n[Image description: {description}]\n\n"
                    described_count += 1
                else:
                    skipped_count += 1
            except Exception as e:
                logger.warning(
                    "%s page %d image xref %d failed to process: %s",
                    filename, i + 1, xref, e,
                )
                skipped_count += 1

        if described_count or skipped_count:
            logger.info(
                "%s page %d: %d image(s) described, %d skipped "
                "(decorative/repeated/failed)",
                filename, i + 1, described_count, skipped_count,
            )

        documents.append(
            Document(
                page_content=page_text,
                metadata={
                    "source": filename,
                    "file_type": "pdf",
                    "page": i + 1,
                    "images_described": described_count,
                    "images_skipped": skipped_count,
                },
            )
        )

    pdf_doc.close()
    return documents