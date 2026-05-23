"""
Tests for all golden HTML pages in pages/*.

For each pages/N.html, the 0-based PDF page index is N + (30 - 13) - 1:
  page 13 → PDF page 30 (1-based) → index 29 (0-based)
  page 14 → PDF page 31 (1-based) → index 30 (0-based)
  …
"""

from __future__ import annotations

import re
import unittest
from pathlib import Path

from src.dom import Heading, ParagraphBlock
from src.html_deserializer import parse_html
from src.normalize import norm_page
from src.pdf_parser import parse_page

PROJECT_ROOT = Path(__file__).parent.parent
PDF_PATH = str(PROJECT_ROOT / "n3685.pdf")
PAGES_DIR = PROJECT_ROOT / "pages"

# HTML page N lives at 1-based PDF page N + (30 - 13).
_PDF_PAGE_OFFSET = 30 - 13

_NAMED_STEM_RE = re.compile(r"^(abstract|contents|foreword|introduction)-(\d+)$")
_NAMED_SECTION_1BASED: dict[str, int] = {
    "abstract": 1,  # abstract-1 → PDF page 1
    "contents": 5,  # contents-1 → PDF page 5
    "foreword": 15,  # foreword-1 → PDF page 15
    "introduction": 16,  # introduction-1 → PDF page 16
}


def _stem_to_pdf_index(stem: str) -> int:
    """Return 0-based PDF page index from a golden-file stem."""
    m = _NAMED_STEM_RE.match(stem)
    if m:
        section, n = m.group(1), int(m.group(2))
        return _NAMED_SECTION_1BASED[section] + n - 2  # 1-based → 0-based
    return int(stem) + _PDF_PAGE_OFFSET - 1  # existing formula


def _stem_sort_key(stem: str) -> tuple[int, int]:
    m = _NAMED_STEM_RE.match(stem)
    if m:
        section, n = m.group(1), int(m.group(2))
        order = {"abstract": 0, "contents": 1, "foreword": 2, "introduction": 3}
        return (order[section], n)
    return (3, int(stem))


def _make_test_class(stem: str, golden_path: Path) -> type:
    pdf_index = _stem_to_pdf_index(stem)
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
for _golden in sorted(PAGES_DIR.glob("*.html"), key=lambda p: _stem_sort_key(p.stem)):
    _stem = _golden.stem
    _cls = _make_test_class(_stem, _golden)
    globals()[_cls.__name__] = _cls
