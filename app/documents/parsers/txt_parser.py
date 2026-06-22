# app/documents/parsers/txt_parser.py
#
# Plain text — no library needed.

from langchain_core.documents import Document


def parse_txt(file_bytes: bytes, filename: str) -> list[Document]:
    """One Document for the whole file."""
    text = file_bytes.decode("utf-8", errors="replace")
    return [
        Document(
            page_content=text,
            metadata={"source": filename, "file_type": "txt"},
        )
    ]