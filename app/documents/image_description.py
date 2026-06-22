# app/documents/image_description.py
#
# Shared vision-based image description, used by pdf_parser.py,
# docx_parser.py, and pptx_parser.py. Confirmed: the PwC GenAI shared
# service supports vision input on our configured model.
#
# This is a real, new cost per content-bearing image — see Section 9
# "Large File Handling" for why this strengthens, not weakens, the
# case for async background processing.

import base64
import logging

from langchain_core.messages import HumanMessage

from app.llm import llm

logger = logging.getLogger("app.documents.image_description")

DESCRIPTION_PROMPT = (
    "Describe this image factually for a document search index. "
    "If it's a chart or graph, state the data shown. If it's a "
    "diagram, describe the structure and flow. Be concise and specific."
)


async def describe_image(image_bytes: bytes, image_format: str = "png") -> str:
    """
    Sends image bytes to the vision-capable LLM and returns a text
    description suitable for embedding into page_content.

    Callers are responsible for deciding WHETHER to call this (see
    should_describe_image) — this function always calls the LLM.
    """
    b64 = base64.b64encode(image_bytes).decode("utf-8")
    message = HumanMessage(content=[
        {"type": "text", "text": DESCRIPTION_PROMPT},
        {
            "type": "image_url",
            "image_url": {"url": f"data:image/{image_format};base64,{b64}"},
        },
    ])
    try:
        response = await llm.ainvoke([message])
        return response.content
    except Exception as e:
        logger.warning("Image description failed: %s", e)
        return "[Image: description unavailable]"