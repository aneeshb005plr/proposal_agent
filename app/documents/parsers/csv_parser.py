# app/documents/parsers/csv_parser.py
#
# CSV extraction via Python's built-in csv module. One Document for
# the whole file, rendered as a markdown table.

import csv
import io
import logging

from langchain_core.documents import Document

logger = logging.getLogger("app.documents.parsers.csv")


def parse_csv(file_bytes: bytes, filename: str) -> list[Document]:
    """One Document for the whole file."""
    text = file_bytes.decode("utf-8", errors="replace")
    reader = csv.reader(io.StringIO(text))
    rows = list(reader)

    if not rows:
        logger.info("%s is empty — no Document produced", filename)
        return []

    header = "| " + " | ".join(rows[0]) + " |"
    separator = "| " + " | ".join("---" for _ in rows[0]) + " |"
    body = "\n".join("| " + " | ".join(row) + " |" for row in rows[1:])
    markdown_table = "\n".join([header, separator, body])

    return [
        Document(
            page_content=markdown_table,
            metadata={"source": filename, "file_type": "csv"},
        )
    ]