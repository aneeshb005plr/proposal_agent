# app/llm.py

import logging

from langchain.chat_models import init_chat_model
from langchain_core.language_models.chat_models import BaseChatModel
from langchain_openai import OpenAIEmbeddings

from app.config import settings

logger = logging.getLogger("app.llm")


def build_llm() -> BaseChatModel:
    """
    Constructs the chat model client. Called once at module import time
    below - not re-created per request.
    """
    logger.info(
        "Initializing LLM client - model=%s base_url=%s",
        settings.GENAI_LLM_MODEL,
        settings.GENAI_BASE_URL,
    )

    return init_chat_model(
        model=settings.GENAI_LLM_MODEL,
        base_url=settings.GENAI_BASE_URL,
        model_provider="openai",
        api_key=settings.GENAI_API_KEY.get_secret_value(),
        temperature=settings.GENAI_TEMPERATURE,
        max_tokens=settings.GENAI_MAX_TOKENS,
    )


def build_embeddings() -> OpenAIEmbeddings:
    """
    Constructs the embeddings client, used by app/knowledge/pipeline.py
    to embed chunks ourselves before inserting directly into MongoDB —
    deliberately NOT via MongoDBAtlasVectorSearch.aadd_documents(),
    which would re-embed internally and double our cost. See pipeline.py
    module docstring for the full reasoning.

    Constructed the same way as the chat model — explicit base_url and
    api_key pointed at the PwC GenAI shared service, not OpenAI's
    default endpoint. Confirmed current API: OpenAIEmbeddings accepts
    base_url as a direct kwarg (resolution order: explicit kwarg first,
    then env vars as fallback) — same pattern as ChatOpenAI/
    init_chat_model.

    No init_embeddings() factory equivalent to init_chat_model() was
    found in current documentation, so this is constructed directly,
    matching the explicit style already used for build_llm() above.
    """
    logger.info(
        "Initializing embeddings client - model=%s base_url=%s",
        settings.GENAI_EMBEDDING_MODEL,
        settings.GENAI_BASE_URL,
    )

    return OpenAIEmbeddings(
        model=settings.GENAI_EMBEDDING_MODEL,
        base_url=settings.GENAI_BASE_URL,
        api_key=settings.GENAI_API_KEY.get_secret_value(),
    )


llm = build_llm()
embeddings = build_embeddings()

# if __name__ == "__main__":