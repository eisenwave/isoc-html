#!/usr/bin/env python3
"""
Convert a single page of the C standard PDF to pretty-printed HTML.

Usage:
    python convert_page.py <pdf_path> <page_number>

<page_number> is the 1-based physical page number within the PDF file
(e.g. 30 for document page 13 of N3685).

The pretty-printed HTML is written to stdout.
"""

from __future__ import annotations

import sys
from pathlib import Path

# Allow running from the project root without installing the package.
sys.path.insert(0, str(Path(__file__).parent))

from src.html_serializer import prettify, serialize
from src.pdf_parser import parse_page


def main() -> None:
    if len(sys.argv) != 3:
        print(f"Usage: {sys.argv[0]} <pdf_path> <page_number>", file=sys.stderr)
        sys.exit(1)

    pdf_path = sys.argv[1]
    try:
        page_number = int(sys.argv[2])
    except ValueError:
        print(f"Error: page_number must be an integer, got {sys.argv[2]!r}", file=sys.stderr)
        sys.exit(1)

    pdf_index = page_number - 1  # convert 1-based file page to 0-based index

    page = parse_page(pdf_path, pdf_index)
    minimal = serialize(page)
    print(prettify(minimal), end="")


if __name__ == "__main__":
    main()
