#!/usr/bin/env python3
"""Regenerate all golden HTML files from the source PDFs.

Golden pages live in per-draft directories under ``pages/``::

    pages/n3685/1.html
    pages/n3685/abstract-1.html
    pages/n3220/74.html
    …

By default every draft with a ``pages/<draft>/`` directory is regenerated
from ``<draft>.pdf`` in the project root.  Pass a PDF path to regenerate
only that draft.

Usage:
    python regenerate_pages.py [pdf_path]

The mapping from golden-file stem to PDF page is derived from the PDF's
page footers, so no front-matter offsets are hard-coded and the same
script works for any draft.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path
from typing import Any, Dict, Tuple

import fitz  # type: ignore[import]

sys.path.insert(0, str(Path(__file__).parent))

from src.html_serializer import prettify, serialize
from src.pdf_parser import parse_page

_FOOTER_Y_MIN = 780.0  # footer region (bottom of page), mirrors pdf_parser

_NAMED_SECTIONS = ("abstract", "contents", "foreword", "introduction")
_NAMED_STEM_RE = re.compile(r"^(abstract|contents|foreword|introduction)-(\d+)$")


def _page_footer(page: Any) -> Tuple[str, str]:
    """Return the ``(clause, page_num)`` pair from a page's footer text."""
    clause = ""
    page_num = ""
    data: Any = page.get_text("dict")
    for block in data.get("blocks", []):
        if "lines" not in block:
            continue
        for line in block["lines"]:
            for span in line["spans"]:
                if span["bbox"][3] < _FOOTER_Y_MIN:
                    continue
                text = span["text"].strip()
                # Right side of the footer: "ClauseName — N"
                if " — " in text:
                    parts = text.rsplit(" — ", 1)
                    clause = parts[0].strip()
                    page_num = parts[1].strip()
    return clause, page_num


def build_stem_to_pdf_index(pdf_path: str) -> Dict[str, int]:
    """Map golden-file stem → 0-based PDF page index for *pdf_path*.

    Derived from the page footers:
    ``abstract-N`` / ``contents-N`` / ``foreword-N`` / ``introduction-N``
    map to the N-th page of that section (by footer clause),
    and ``N`` (a document page number) maps to the page whose footer page
    number is ``N``.
    """
    doc: Any = fitz.open(pdf_path)
    result: Dict[str, int] = {}
    counters: Dict[str, int] = {name: 0 for name in _NAMED_SECTIONS}
    for idx in range(doc.page_count):
        clause, page_num = _page_footer(doc[idx])
        clause_key = clause.lower()
        if clause_key in counters:
            counters[clause_key] += 1
            result[f"{clause_key}-{counters[clause_key]}"] = idx
        elif page_num.isdigit():
            result[page_num] = idx
    return result


def stem_sort_key(stem: str) -> Tuple[int, int]:
    """Return a sort key for a golden-file stem (named pages first, then numbers)."""
    m = _NAMED_STEM_RE.match(stem)
    if m:
        section, n = m.group(1), int(m.group(2))
        order = {name: i for i, name in enumerate(_NAMED_SECTIONS)}
        return (order[section], n)
    if stem.isdigit():
        return (len(_NAMED_SECTIONS), int(stem))
    return (len(_NAMED_SECTIONS) + 1, 0)


def main() -> None:
    script_dir = Path(__file__).parent
    pages_root = script_dir / "pages"

    if len(sys.argv) > 1:
        # Regenerate a single draft only.
        drafts = [Path(sys.argv[1]).stem]
    elif pages_root.is_dir():
        # Regenerate every draft that has a golden-pages directory.
        drafts = sorted(d.name for d in pages_root.iterdir() if d.is_dir())
    else:
        drafts = []

    if not drafts:
        sys.exit(f"No golden pages directories found under {pages_root}")

    total_pages = 0
    for draft in drafts:
        pdf_path = script_dir / f"{draft}.pdf"
        pages_dir = pages_root / draft

        if not pdf_path.exists():
            print(f"Skipping {draft}: PDF not found ({pdf_path.name})")
            continue
        if not pages_dir.is_dir():
            print(f"Skipping {draft}: no golden pages directory {pages_dir}")
            continue

        stem_to_index = build_stem_to_pdf_index(str(pdf_path))
        html_files = sorted(pages_dir.glob("*.html"), key=lambda p: stem_sort_key(p.stem))
        if not html_files:
            print(f"Skipping {draft}: no HTML files in {pages_dir}")
            continue

        for html_path in html_files:
            stem = html_path.stem
            if stem not in stem_to_index:
                print(f"Skipping {html_path.name}: no PDF page found for this stem")
                continue
            pdf_index = stem_to_index[stem]

            print(
                f"Regenerating {html_path.relative_to(script_dir)} from PDF page {pdf_index + 1}..."
            )
            page = parse_page(str(pdf_path), pdf_index)
            html_path.write_text(prettify(serialize(page)), encoding="utf-8")
            total_pages += 1

    print(f"Done ({total_pages} page(s) regenerated).")


if __name__ == "__main__":
    main()
