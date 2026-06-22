# app/documents/parser.py
#
# Public interface for document parsing. Dispatches to the correct
# format-specific parser based on file extension. Each parser lives
# in app/documents/parsers/ and is independently testable.
#
# No LLM calls anywhere in this module or its sub-parsers — see
# architecture discussion. Tables are recovered mechanically.
# Images/diagrams are detected and counted in metadata, but their
# content is not extracted — deliberately deferred, not a silent gap.
#
# Returns LangChain's standard Document shape (page_content +
# metadata) so output plugs directly into text splitters and vector
# stores in later steps.

from pathlib import Path

from langchain_core.documents import Document

from app.documents.parsers.csv_parser import parse_csv
from app.documents.parsers.docx_parser import parse_docx
from app.documents.parsers.pdf_parser import parse_pdf
from app.documents.parsers.pptx_parser import parse_pptx
from app.documents.parsers.txt_parser import parse_txt
from app.documents.parsers.xlsx_parser import parse_xlsx


class UnsupportedFileTypeError(Exception):
    """Raised when a file type is not supported by the parser."""
    pass


_PARSERS = {
    ".pdf": parse_pdf,
    ".docx": parse_docx,
    ".pptx": parse_pptx,
    ".xlsx": parse_xlsx,
    ".csv": parse_csv,
    ".txt": parse_txt,
}


def parse_document(file_bytes: bytes, filename: str) -> list[Document]:
    """
    Parses raw file bytes into a list of LangChain Document objects.

    One Document per logical unit of the source file:
      - PDF:  one Document per page
      - DOCX: one Document for the whole file
      - PPTX: one Document per slide
      - XLSX: one Document per sheet
      - CSV:  one Document for the whole file
      - TXT:  one Document for the whole file

    Raises UnsupportedFileTypeError for .doc and any other
    unrecognised extension.
    """
    extension = Path(filename).suffix.lower()

    if extension == ".doc":
        raise UnsupportedFileTypeError(
            "Legacy .doc (Word 97-2003) format is not supported. "
            "No reliable, dependency-light Python library exists for "
            "this binary format. Please convert to .docx or PDF and "
            "re-upload."
        )

    parser_fn = _PARSERS.get(extension)
    if parser_fn is None:
        raise UnsupportedFileTypeError(
            f"File type '{extension}' is not supported. "
            f"Supported types: {', '.join(_PARSERS.keys())}"
        )

    return parser_fn(file_bytes, filename)