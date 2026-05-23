#!/usr/bin/env python3
"""Regenerate all HTML files in pages/ from the source PDF.

Naming conventions:
  pages/N.html          -> 1-based PDF page N + 17
  pages/abstract-N.html -> 1-based PDF page N        (N=1..4)
  pages/contents-N.html -> 1-based PDF page N+4      (N=1..10)
  pages/foreword-N.html -> 1-based PDF page N+14     (N=1)
  pages/introduction-N.html -> 1-based PDF page N+15 (N=1..2)

Usage:
    python regenerate_pages.py [pdf_path]

<pdf_path> defaults to n3685.pdf in the same directory as this script.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from src.html_serializer import prettify, serialize
from src.pdf_parser import parse_page

_PDF_PAGE_OFFSET = 30 - 13

_NAMED_STEM_RE = re.compile(r"^(abstract|contents|foreword|introduction)-(\d+)$")
_NAMED_SECTION_1BASED: dict[str, int] = {
    "abstract": 1,
    "contents": 5,
    "foreword": 15,
    "introduction": 16,
}


def _stem_to_pdf_1based(stem: str) -> int:
    m = _NAMED_STEM_RE.match(stem)
    if m:
        section, n = m.group(1), int(m.group(2))
        return _NAMED_SECTION_1BASED[section] + n - 1
    return int(stem) + _PDF_PAGE_OFFSET


def _stem_sort_key(stem: str) -> tuple[int, int]:
    m = _NAMED_STEM_RE.match(stem)
    if m:
        section, n = m.group(1), int(m.group(2))
        order = {"abstract": 0, "contents": 1, "foreword": 2, "introduction": 3}
        return (order[section], n)
    return (3, int(stem))


def main() -> None:
    script_dir = Path(__file__).parent
    pdf_path = Path(sys.argv[1]) if len(sys.argv) > 1 else script_dir / "n3685.pdf"

    if not pdf_path.exists():
        sys.exit(f"PDF not found: {pdf_path}")

    pages_dir = script_dir / "pages"
    html_files = sorted(pages_dir.glob("*.html"), key=lambda p: _stem_sort_key(p.stem))

    if not html_files:
        sys.exit(f"No HTML files found in {pages_dir}")

    for html_path in html_files:
        stem = html_path.stem
        pdf_page_1based = _stem_to_pdf_1based(stem)
        pdf_index = pdf_page_1based - 1

        print(f"Regenerating pages/{html_path.name} from PDF page {pdf_page_1based}...")
        page = parse_page(str(pdf_path), pdf_index)
        html_path.write_text(prettify(serialize(page)), encoding="utf-8")

    print(f"Done ({len(html_files)} page(s) regenerated).")


if __name__ == "__main__":
    main()
