#!/usr/bin/env python3
"""Generate HTML for an entire ISO C draft from a PDF file.

Usage:
    python generate.py <path/to/draft.pdf> > output.html

All pages are parsed in order.  Cross-page list and code-block continuations
are merged automatically.  Footnotes from every page are collected into a
single section at the end of the document.

The output is written to stdout as UTF-8-encoded minimal HTML (no added
indentation whitespace).  Pipe through an HTML formatter if human-readable
output is required.
"""

from __future__ import annotations

import sys
from pathlib import Path

# Allow running from the project root without installing the package.
sys.path.insert(0, str(Path(__file__).parent))

from src.document import parse_document
from src.html_serializer import serialize_document


def main() -> None:
    if len(sys.argv) < 2:
        sys.exit(f"Usage: {Path(sys.argv[0]).name} <pdf_path>")

    pdf_path = Path(sys.argv[1])
    if not pdf_path.exists():
        sys.exit(f"error: PDF not found: {pdf_path}")

    doc = parse_document(str(pdf_path))
    html = serialize_document(doc.sections, doc.footnotes, doc.draft_id)
    sys.stdout.buffer.write(html.encode("utf-8"))


if __name__ == "__main__":
    main()
