# app/documents/chunker.py
#
# Chunking for knowledge-base indexing (Step 6 pipeline) and, later,
# for any other place that needs to split parsed Documents before
# embedding.
#
# DESIGN DECISIONS (verified against current 2026 guidance, not
# assumed — see ADR in the architecture doc for full detail):
#
#   - RecursiveCharacterTextSplitter is the baseline workhorse,
#     consistently recommended across current sources as the
#     starting point for any RAG chunking pipeline.
#   - MarkdownHeaderTextSplitter runs FIRST for prose-like content
#     (PDF, DOCX), since our parsers already deliberately produce
#     markdown headers ('#', '##') specifically so a header-aware
#     splitter can use them as real semantic boundaries, not
#     arbitrary character-count cuts. RecursiveCharacterTextSplitter
#     then runs within each header-delimited section to enforce a
#     hard size ceiling.
#   - Semantic chunking (SemanticChunker / embedding-based methods)
#     is DELIBERATELY NOT used. Current 2026 evidence is mixed-to-
#     negative for this kind of content (a NAACL 2025 Findings paper
#     found fixed-size chunking matched or beat semantic chunking;
#     a Feb 2026 benchmark placed recursive 512-token splitting above
#     semantic chunking at 69% vs 54% accuracy), and semantic chunking
#     requires an embedding call PER SENTENCE just to do the chunking
#     itself — a real, avoidable cost given our budget constraints.
#   - PPTX/XLSX: each Document is already one slide or one sheet —
#     a natural, pre-existing chunk boundary, arguably better than
#     anything a generic text splitter would produce. These are only
#     run through the splitter as a FALLBACK if a single slide/sheet
#     exceeds the size ceiling on its own (e.g. an unusually dense
#     slide or a huge table).
#
# Chunk size and overlap are configurable (app/config.py), not
# hardcoded — starting defaults of 500 tokens / 50 token overlap are
# themselves an evidence-informed STARTING POINT (bracketed by two
# real 2026 benchmarks at 400 and 512 tokens), explicitly expected to
# be tuned later against real retrieval quality once we have it —
# not treated as a final answer, same as every other unverified-by-
# real-use number in this project.

import logging

from langchain_core.documents import Document
from langchain_text_splitters import (
    MarkdownHeaderTextSplitter,
    RecursiveCharacterTextSplitter,
)

from app.config import settings

logger = logging.getLogger("app.documents.chunker")

# Markdown header levels our parsers actually produce — see
# pdf_parser.py / docx_parser.py, which emit '#' through roughly
# '####' depending on source heading depth.
_MARKDOWN_HEADERS_TO_SPLIT_ON = [
    ("#", "h1"),
    ("##", "h2"),
    ("###", "h3"),
    ("####", "h4"),
]

# File types where each input Document is prose-like and benefits
# from header-aware splitting first.
_HEADER_AWARE_TYPES = {"pdf", "docx", "txt"}

# File types where each input Document is already a natural chunk
# (one slide, one sheet) and should only be split further as a
# fallback for oversized individual units.
_NATURAL_UNIT_TYPES = {"pptx", "xlsx", "csv"}


def _build_recursive_splitter() -> RecursiveCharacterTextSplitter:
    """
    Built fresh per call rather than as a module-level singleton —
    RecursiveCharacterTextSplitter is cheap to construct and this
    avoids any risk of shared mutable state across concurrent
    chunking calls for different documents.
    """
    return RecursiveCharacterTextSplitter(
        chunk_size=settings.CHUNK_SIZE_TOKENS,
        chunk_overlap=settings.CHUNK_OVERLAP_TOKENS,
        # Default separator cascade (paragraph → line → sentence →
        # word) — confirmed current default behavior, not overridden,
        # since it already does the right thing: prefer splitting on
        # larger structural boundaries before falling back to smaller
        # ones.
    )


def _build_header_splitter() -> MarkdownHeaderTextSplitter:
    return MarkdownHeaderTextSplitter(
        headers_to_split_on=_MARKDOWN_HEADERS_TO_SPLIT_ON,
        # Keep header text in the resulting chunk content (not just
        # in metadata) — a chunk that says "Tone of voice should be
        # collaborative" is more useful standalone than a chunk that
        # has lost the heading context it appeared under.
        strip_headers=False,
    )


def chunk_document(document: Document) -> list[Document]:
    """
    Splits one parsed Document (one page, slide, sheet, or whole-file
    unit, per app/documents/parser.py) into one or more chunks ready
    for embedding.

    Routing by file_type, per the design decisions above:
      - prose-like types (pdf, docx, txt): MarkdownHeaderTextSplitter
        first, then RecursiveCharacterTextSplitter within each section
      - natural-unit types (pptx, xlsx, csv): kept as a single chunk
        unless it exceeds the size ceiling, in which case
        RecursiveCharacterTextSplitter is applied as a fallback

    Metadata from the source Document is preserved and merged into
    every resulting chunk, so chunk.metadata still carries source,
    file_type, page/slide/sheet, etc. — exactly the same fields the
    rest of the pipeline (knowledge_chunks indexing, citations) needs.
    """
    file_type = document.metadata.get("file_type", "")

    if file_type in _HEADER_AWARE_TYPES:
        return _chunk_header_aware(document)
    elif file_type in _NATURAL_UNIT_TYPES:
        return _chunk_natural_unit(document)
    else:
        logger.warning(
            "Unrecognized file_type '%s' for chunking — falling back "
            "to plain recursive splitting", file_type,
        )
        return _split_with_metadata(document, _build_recursive_splitter())


def _chunk_header_aware(document: Document) -> list[Document]:
    header_splitter = _build_header_splitter()
    header_sections = header_splitter.split_text(document.page_content)

    if not header_sections:
        # No markdown headers found in this document at all (e.g. a
        # plain TXT file with no '#' anywhere) — fall back to plain
        # recursive splitting on the whole content.
        return _split_with_metadata(document, _build_recursive_splitter())

    recursive_splitter = _build_recursive_splitter()
    chunks: list[Document] = []

    for section in header_sections:
        # Merge the original document's metadata with whatever header
        # context (h1/h2/h3/h4 values) MarkdownHeaderTextSplitter added
        # for this section, then further split if the section itself
        # is still too large.
        merged_metadata = {**document.metadata, **section.metadata}
        section_doc = Document(
            page_content=section.page_content, metadata=merged_metadata
        )
        chunks.extend(_split_with_metadata(section_doc, recursive_splitter))

    return chunks


def _chunk_natural_unit(document: Document) -> list[Document]:
    """
    PPTX/XLSX/CSV: each input Document (one slide, one sheet, one
    file) is already a coherent, natural chunk. Only split further
    if it exceeds the configured size ceiling — most will not.
    """
    approx_char_limit = settings.CHUNK_SIZE_TOKENS * 4  # rough chars-
    # per-token heuristic, consistent with how the rest of this
    # project has used a 4-chars-per-token approximation when an
    # exact tokenizer count isn't readily available

    if len(document.page_content) <= approx_char_limit:
        return [document]

    logger.info(
        "%s (%s) exceeded the natural-unit size ceiling (%d chars) — "
        "splitting further",
        document.metadata.get("source", "unknown"),
        document.metadata.get("file_type", ""),
        approx_char_limit,
    )
    return _split_with_metadata(document, _build_recursive_splitter())


def _split_with_metadata(
    document: Document, splitter: RecursiveCharacterTextSplitter
) -> list[Document]:
    """
    Runs RecursiveCharacterTextSplitter.split_documents() on a single
    Document, which correctly propagates the original metadata to
    every resulting chunk — confirmed current LangChain behavior, not
    something we need to hand-roll ourselves.
    """
    return splitter.split_documents([document])


def chunk_documents(documents: list[Document]) -> list[Document]:
    """
    Convenience wrapper — chunks a full list of Documents (e.g. every
    page of a PDF, or every slide of a PPTX) and returns the flattened
    list of all resulting chunks, ready for embedding.
    """
    all_chunks: list[Document] = []
    for doc in documents:
        all_chunks.extend(chunk_document(doc))
    return all_chunks