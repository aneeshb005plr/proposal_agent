# app/documents/image_description.py — updated

import base64
import logging
from io import BytesIO

from langchain_core.messages import HumanMessage
from PIL import Image as PILImage

from app.llm import llm

logger = logging.getLogger("app.documents.image_description")

DESCRIPTION_PROMPT = (
    "Describe this image factually for a document search index. "
    "If it's a chart or graph, state the data shown. If it's a "
    "diagram, describe the structure and flow. Be concise and specific."
)


def _ensure_supported_format(image_bytes: bytes) -> bytes:
    """
    The vision API only accepts png, webp, gif, jpeg — confirmed via
    a real, reproduced 400 error: PowerPoint commonly embeds EMF/WMF
    (Windows vector metafile) images, especially for charts/diagrams,
    which Pillow can often open but the vision API rejects outright.

    Rather than trust the source format is already accepted, this
    ALWAYS re-encodes via Pillow to PNG before sending — a small,
    guaranteed-safe normalization step that costs little (images are
    already small enough to pass the content-image size filter) and
    eliminates this whole class of failure regardless of source format.
    """
    with PILImage.open(BytesIO(image_bytes)) as img:
        # Convert to RGB first — PNG re-encoding can fail on some
        # color modes (e.g. CMYK, or palette modes EMF sometimes
        # uses) without this normalization step.
        if img.mode not in ("RGB", "RGBA"):
            img = img.convert("RGB")
        output = BytesIO()
        img.save(output, format="PNG")
        return output.getvalue()


async def describe_image(image_bytes: bytes, image_format: str = "png") -> str:
    """
    Sends image bytes to the vision-capable LLM and returns a text
    description suitable for embedding into page_content.

    Always normalizes to PNG first via _ensure_supported_format,
    regardless of the source format — eliminates the EMF/WMF
    rejection failure mode entirely rather than special-casing it.
    """
    try:
        normalized_bytes = _ensure_supported_format(image_bytes)
    except Exception as e:
        logger.warning(
            "Image format normalization failed (%s) — skipping "
            "description for this image.", e,
        )
        return "[Image: description unavailable]"

    b64 = base64.b64encode(normalized_bytes).decode("utf-8")
    message = HumanMessage(content=[
        {"type": "text", "text": DESCRIPTION_PROMPT},
        {
            "type": "image_url",
            "image_url": {"url": f"data:image/png;base64,{b64}"},
        },
    ])
    try:
        response = await llm.ainvoke([message])
        return response.content
    except Exception as e:
        logger.warning("Image description failed: %s", e)
        return "[Image: description unavailable]"