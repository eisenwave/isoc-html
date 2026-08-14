"""
Tests for all golden HTML pages in pages/<draft>/.

Golden pages are organised per draft so several drafts can be tested from
the same tree::

    pages/n3685/1.html
    pages/n3685/abstract-1.html
    pages/n3220/74.html
    …

The ``--pdf`` option selects the draft.  The golden directory ``pages/<stem>``
and the stem → PDF-page mapping are derived from the PDF's page footers
(see ``build_stem_to_pdf_index`` below), so no hard-coded front-matter
offsets are needed.
"""

from __future__ import annotations

import re
import unittest
from pathlib import Path
from typing import Any, Dict, Tuple

import fitz  # type: ignore[import]

from src.dom import Heading, ParagraphBlock
from src.html_deserializer import parse_html
from src.normalize import norm_page
from src.pdf_parser import parse_page
from tests.conftest import get_pdf_path

# ---------------------------------------------------------------------------
# Golden-page mapping
# ---------------------------------------------------------------------------

_FOOTER_Y_MIN = 780.0  # footer region (bottom of page), mirrors pdf_parser

_NAMED_SECTIONS = ("abstract", "contents", "foreword", "introduction")
_NAMED_STEM_RE = re.compile(r"^(abstract|contents|foreword|introduction)-(\d+)$")


def golden_pages_dir(pdf_path: str) -> Path:
    """Return the golden-pages directory for *pdf_path* (``pages/<stem>/``)."""
    stem = Path(pdf_path).stem
    return Path(__file__).resolve().parent.parent / "pages" / stem


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

    The PDF is opened once and every page footer is inspected (cheap text
    extraction, no full parse).  Unmapped pages (cover, blank pages) are
    simply not part of the mapping:

    * ``abstract-N`` / ``contents-N`` / ``foreword-N`` / ``introduction-N``
      map to the N-th page of that section (identified by the footer clause);
    * ``N`` (a document page number) maps to the page whose footer page
      number is ``N``.

    The named pages use Roman-numeral footers in the PDF; the numbered
    document pages use Arabic numerals, so the two groups never collide.
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
    """Return a sort key for a golden-file stem.

    Named pages sort first (in the order they appear in the standard), then
    numbered document pages in numeric order.  Unrecognised stems sort last
    in lexicographic order.
    """
    m = _NAMED_STEM_RE.match(stem)
    if m:
        section, n = m.group(1), int(m.group(2))
        order = {name: i for i, name in enumerate(_NAMED_SECTIONS)}
        return (order[section], n)
    if stem.isdigit():
        return (len(_NAMED_SECTIONS), int(stem))
    return (len(_NAMED_SECTIONS) + 1, 0)


PDF_PATH = get_pdf_path()
PAGES_DIR = golden_pages_dir(PDF_PATH)
STEM_TO_PDF_INDEX = build_stem_to_pdf_index(PDF_PATH)


def _make_test_class(stem: str, golden_path: Path) -> type:
    pdf_index = STEM_TO_PDF_INDEX[stem]
    class_name = "TestPage" + stem.replace("-", "_")

    class _PageTest(unittest.TestCase):
        @classmethod
        def setUpClass(cls) -> None:
            cls.pdf_page = norm_page(parse_page(PDF_PATH, pdf_index))
            cls.golden_page = norm_page(parse_html(golden_path.read_text(encoding="utf-8")))

        def test_header(self) -> None:
            self.assertEqual(self.pdf_page.header, self.golden_page.header)

        def test_footer(self) -> None:
            self.assertEqual(self.pdf_page.footer, self.golden_page.footer)

        def test_main_element_count(self) -> None:
            self.assertEqual(
                len(self.pdf_page.main),
                len(self.golden_page.main),
                f"PDF main elements: {len(self.pdf_page.main)}, "
                f"Golden: {len(self.golden_page.main)}",
            )

        def test_main_element_types(self) -> None:
            for i, (p, g) in enumerate(zip(self.pdf_page.main, self.golden_page.main)):
                self.assertEqual(
                    type(p),
                    type(g),
                    f"Element {i}: PDF={type(p).__name__}, Golden={type(g).__name__}",
                )

        def test_headings(self) -> None:
            pdf_h = [e for e in self.pdf_page.main if isinstance(e, Heading)]
            gold_h = [e for e in self.golden_page.main if isinstance(e, Heading)]
            self.assertEqual(len(pdf_h), len(gold_h))
            for ph, gh in zip(pdf_h, gold_h):
                self.assertEqual(ph.section_id, gh.section_id, "section_id mismatch")
                self.assertEqual(ph.title, gh.title, f"title mismatch for {ph.section_id}")
                self.assertEqual(ph.level, gh.level)

        def test_paragraph_ids(self) -> None:
            pdf_p = [e for e in self.pdf_page.main if isinstance(e, ParagraphBlock)]
            gold_p = [e for e in self.golden_page.main if isinstance(e, ParagraphBlock)]
            self.assertEqual(len(pdf_p), len(gold_p))
            for pp, gp in zip(pdf_p, gold_p):
                self.assertEqual(pp.id, gp.id, "paragraph id mismatch")
                self.assertEqual(pp.num, gp.num, f"paragraph num mismatch for {pp.id}")

        def test_paragraph_children_types(self) -> None:
            pdf_p = [e for e in self.pdf_page.main if isinstance(e, ParagraphBlock)]
            gold_p = [e for e in self.golden_page.main if isinstance(e, ParagraphBlock)]
            for pp, gp in zip(pdf_p, gold_p):
                self.assertEqual(
                    len(pp.children),
                    len(gp.children),
                    f"Paragraph {pp.id}: child count mismatch "
                    f"(pdf={len(pp.children)}, golden={len(gp.children)})",
                )
                for j, (pc, gc) in enumerate(zip(pp.children, gp.children)):
                    self.assertEqual(
                        type(pc),
                        type(gc),
                        f"Paragraph {pp.id} child {j}: "
                        f"pdf={type(pc).__name__}, golden={type(gc).__name__}",
                    )

        def test_full_equality(self) -> None:
            self.assertEqual(len(self.pdf_page.main), len(self.golden_page.main))
            for i, (p_elem, g_elem) in enumerate(zip(self.pdf_page.main, self.golden_page.main)):
                self.assertEqual(
                    p_elem,
                    g_elem,
                    f"Main element {i} differs:\n  PDF:    {p_elem!r}\n  Golden: {g_elem!r}",
                )

        def test_footnotes(self) -> None:
            self.assertEqual(
                len(self.pdf_page.footnotes),
                len(self.golden_page.footnotes),
            )
            for pf, gf in zip(self.pdf_page.footnotes, self.golden_page.footnotes):
                self.assertEqual(pf.id, gf.id, "footnote id mismatch")
                self.assertEqual(
                    pf,
                    gf,
                    f"Footnote {pf.id} differs:\n  PDF:    {pf!r}\n  Golden: {gf!r}",
                )

    _PageTest.__name__ = class_name
    _PageTest.__qualname__ = class_name
    return _PageTest


# Discover all golden files and register a test class for each.
_cls = None  # loop variable; deleted below so pytest does not collect it twice
for _golden in sorted(PAGES_DIR.glob("*.html"), key=lambda p: stem_sort_key(p.stem)):
    _stem = _golden.stem
    if _stem not in STEM_TO_PDF_INDEX:
        print(f"Warning: no PDF page found for golden page {_golden.name}; skipping")
        continue
    _cls = _make_test_class(_stem, _golden)
    globals()[_cls.__name__] = _cls

# Remove the loop variable so it is not collected as a duplicate test class
# (pytest collects every unittest.TestCase subclass found in module globals).
del _cls
