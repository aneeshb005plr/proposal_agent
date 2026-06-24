# app/documents/parsers/pdf_parser.py
#
# Updated: images are now extracted (not just counted) and, when
# they pass the content-image filter, described via vision LLM and
# inserted into page_content so their information becomes searchable.
#
# BUG FOUND AND FIXED: pymupdf4llm.to_markdown() inserts its OWN
# placeholder text directly into page_dict["text"] whenever its
# layout engine detects an image it wasn't told to extract/embed
# (write_images=False is our default — confirmed from the library's
# own documentation). This placeholder
# ("<== picture [WxH] intentionally omitted ==>") is INDEPENDENT of
# and runs BEFORE our own image extraction loop below. Without
# stripping it, chunks could contain both this redundant placeholder
# AND our own real [Image description: ...] text, disconnected from
# each other. Since we already extract and describe images ourselves
# via page.get_images() + vision LLM, this placeholder is pure noise
# and is stripped before any further processing.
#
# KNOWN LIMITATION, accepted: pymupdf4llm's own image-detection count
# (filtered by its documented 5%-of-page-size significance threshold)
# does not necessarily match page.get_images()'s raw count (every
# embedded image object, regardless of visual significance). After
# stripping the placeholder text, images_described/images_skipped in
# metadata reflect ONLY page.get_images()'s count — the more complete
# lower-level signal — not a reconciled count between both
# mechanisms. This means metadata counts may not match what a human
# visually counts on the page in edge cases; not fully resolved here.

import logging
import re

import pymupdf
import pymupdf4llm
from langchain_core.documents import Document

from app.documents.image_description import describe_image
from app.documents.image_filter import reset_seen_images, should_describe_image

logger = logging.getLogger("app.documents.parsers.pdf")

# Confirmed against one real observed string:
# "<== picture [510 x 342] intentionally omitted ==>"
# Kept permissive ([\w\s]*) around "picture" in case a differently-
# worded variant exists for vector graphics vs. raster images — not
# yet confirmed either way; tighten or expand once seen in practice.
_OMITTED_IMAGE_PATTERN = re.compile(
    r"<==\s*[\w\s]*\[\d+\s*x\s*\d+\]\s*intentionally omitted\s*==>",
    re.IGNORECASE,
)


def _strip_omitted_placeholders(text: str) -> str:
    """Removes pymupdf4llm's own image-omission placeholder text.
    See module docstring for why this is safe and necessary."""
    cleaned = _OMITTED_IMAGE_PATTERN.sub("", text)
    return re.sub(r"\n{3,}", "\n\n", cleaned).strip()


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

        # Strip pymupdf4llm's own placeholder FIRST, before our own
        # image descriptions get appended below — prevents the two
        # mechanisms' output from intermixing.
        page_text = _strip_omitted_placeholders(page_dict["text"])

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