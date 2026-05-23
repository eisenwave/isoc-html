"""Document-level regression tests.

These tests build the full merged document from the PDF (exactly as
``generate.py`` does) and run structural/content checks on the resulting
HTML using BeautifulSoup.

Adding a new check:
  1. Write a ``test_*`` method on ``TestDocument``.
  2. Use the helper methods on ``self`` for common queries:
       _find_li(text)            → first <li> whose text contains *text*
       _find_element(tag, text)  → first <tag> whose text contains *text*
       _child_position(elem)     → 1-based index among same-tag siblings
"""

from __future__ import annotations

import unittest
from pathlib import Path
from typing import Optional

from bs4 import BeautifulSoup, Tag

PROJECT_ROOT = Path(__file__).parent.parent
PDF_PATH = str(PROJECT_ROOT / "n3685.pdf")


class TestDocument(unittest.TestCase):
    """Structural and content checks on the full generated document."""

    soup: BeautifulSoup  # populated in setUpClass

    @classmethod
    def setUpClass(cls) -> None:
        from src.document import parse_document
        from src.html_serializer import serialize_document

        doc = parse_document(PDF_PATH)
        html = serialize_document(doc.sections, doc.footnotes, doc.draft_id)
        cls.soup = BeautifulSoup(html, "html.parser")

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _find_element(self, tag: str, text: str) -> Tag:
        """Return the first *tag* element whose full text contains *text*.

        Fails the test immediately if no match is found.
        """
        match: Optional[Tag] = next(
            (t for t in self.soup.find_all(tag) if text in t.get_text()),
            None,
        )
        self.assertIsNotNone(match, f"No <{tag}> found containing: {text!r}")
        assert match is not None  # narrow for type checker
        return match

    def _find_li(self, text: str) -> Tag:
        """Return the first <li> whose full text contains *text*."""
        return self._find_element("li", text)

    def _child_position(self, elem: Tag) -> int:
        """Return the 1-based index of *elem* among its same-tag siblings."""
        parent = elem.parent
        self.assertIsNotNone(parent, f"<{elem.name}> has no parent element")
        assert parent is not None
        siblings = [c for c in parent.children if isinstance(c, Tag) and c.name == elem.name]
        return siblings.index(elem) + 1

    # ------------------------------------------------------------------
    # Tests
    # ------------------------------------------------------------------

    def test_synopsis_headings_have_pre_with_include(self) -> None:
        """Every heading with text 'Synopsis' must be immediately followed by a
        paragraph block (div.p) that contains a <pre> code block, and that
        <pre> must include a syntax-highlighted '#include' directive
        (``<span class="tk-kw">#include</span>``).

        This invariant holds for all library synopses in the standard.
        """

        def is_synopsis_heading(tag: Tag) -> bool:
            if tag.name in ("h1", "h2", "h3", "h4", "h5", "h6"):
                return tag.get_text().strip() == "Synopsis"
            if tag.name == "div" and tag.get("role") == "heading":
                return tag.get_text().strip() == "Synopsis"
            return False

        failures: list[str] = []
        for elem in self.soup.find_all(True):
            if not is_synopsis_heading(elem):
                continue
            assert isinstance(elem, Tag)
            next_p = elem.find_next_sibling("div", class_="p")
            pid = next_p.get("id", "?") if next_p else "?"
            if next_p is None:
                failures.append(f"Synopsis near {elem.get('id', '?')!r}: no following div.p")
                continue
            pre = next_p.find("pre")
            if pre is None:
                failures.append(f"Synopsis → {pid!r}: div.p has no <pre> block")
                continue
            assert isinstance(pre, Tag)
            has_include = any(
                span.get_text() == "#include" for span in pre.find_all("span", class_="tk-kw")
            )
            if not has_include:
                failures.append(
                    f'Synopsis → {pid!r}: <pre> has no <span class="tk-kw">#include</span>'
                )

        self.assertEqual(
            failures,
            [],
            "Synopsis headings without a proper pre+#include block:\n"
            + "\n".join(f"  {f}" for f in failures),
        )

    def test_translation_phase_external_refs_is_eighth(self) -> None:
        """'All external object and function references are resolved'
        must be the eighth <li> in its parent <ol> (translation phase 8;
        the page-boundary continuation of phase 5 is merged into phase 5's item).
        """
        li = self._find_li("All external object and function references are resolved")
        parent = li.parent
        self.assertIsNotNone(parent)
        assert parent is not None
        self.assertEqual(parent.name, "ol", f"Expected parent <ol>, got <{parent.name}>")
        self.assertEqual(self._child_position(li), 8)

    def test_grammar_cast_expression_structure(self) -> None:
        """cast-expression grammar must use <dl class="grammar"> with correct
        <dt><a id="nonterminal-cast-expression">…</a>:</dt> structure; nonterminals
        in productions must be <a href="#nonterminal-..."><i class="nonterminal">.
        """
        # Find the <dl class="grammar"> whose <dt> defines cast-expression
        dl: Optional[Tag] = next(
            (
                t
                for t in self.soup.find_all("dl", class_="grammar")
                if t.find("a", id="nonterminal-cast-expression")
            ),
            None,
        )
        self.assertIsNotNone(dl, "No <dl class='grammar'> found with a#nonterminal-cast-expression")
        assert dl is not None

        # The <dt> must contain <a id="nonterminal-cast-expression">
        anchor = dl.find("a", id="nonterminal-cast-expression")
        self.assertIsNotNone(
            anchor, "<dl class='grammar'> missing <a id='nonterminal-cast-expression'>"
        )
        assert isinstance(anchor, Tag)
        dt = anchor.parent
        self.assertIsNotNone(dt, "<a id='nonterminal-cast-expression'> has no parent")
        assert isinstance(dt, Tag)
        self.assertEqual(dt.name, "dt", f"Expected <dt> parent, got <{dt.name}>")

        # The <dt> must also have a colon visible in its text
        dt_text = dt.get_text()
        self.assertIn(":", dt_text, "<dt> text must contain ':'")

        # The <dt> must contain <i class="nonterminal">cast-expression</i>
        i_tag = dt.find("i", class_="nonterminal")
        self.assertIsNotNone(i_tag, "<dt> missing <i class='nonterminal'>")
        assert isinstance(i_tag, Tag)
        self.assertEqual(i_tag.get_text(), "cast-expression")

        # Must have at least two <dd> elements (productions)
        dds = dl.find_all("dd", recursive=False)
        self.assertGreaterEqual(len(dds), 2, f"Expected ≥2 <dd> elements, got {len(dds)}")

        # Every italic nonterminal in <dd> must be <a href="#nonterminal-..."><i class="nonterminal">
        for dd in dds:
            assert isinstance(dd, Tag)
            for i_tag in dd.find_all("i", class_="nonterminal"):
                assert isinstance(i_tag, Tag)
                parent_a = i_tag.parent
                self.assertIsNotNone(parent_a, "<i class='nonterminal'> has no parent")
                assert isinstance(parent_a, Tag)
                self.assertEqual(
                    parent_a.name,
                    "a",
                    f"Nonterminal '{i_tag.get_text()}' must be wrapped in <a>, "
                    f"but parent is <{parent_a.name}>",
                )
                href = parent_a.get("href", "")
                self.assertTrue(
                    str(href).startswith("#nonterminal-"),
                    f"<a> for nonterminal '{i_tag.get_text()}' has href={href!r}, "
                    "expected '#nonterminal-...'",
                )

    def test_grammar_multiplicative_expression_continuation(self) -> None:
        """multiplicative-expression grammar must have 4 productions after merging
        pages 86 (1 production: cast-expression) and 87 (3 productions: mult*cast,
        mult/cast, mult%cast) across the page boundary.
        """
        # Find the <dl class="grammar"> whose <dt> defines multiplicative-expression
        dl: Optional[Tag] = next(
            (
                t
                for t in self.soup.find_all("dl", class_="grammar")
                if t.find("a", id="nonterminal-multiplicative-expression")
            ),
            None,
        )
        self.assertIsNotNone(
            dl,
            "No <dl class='grammar'> found with a#nonterminal-multiplicative-expression",
        )
        assert dl is not None

        dds = dl.find_all("dd", recursive=False)
        self.assertEqual(
            len(dds),
            4,
            f"multiplicative-expression grammar should have 4 productions (1 from "
            f"page 86 + 3 from page 87), got {len(dds)}",
        )

        # First production: cast-expression (from page 86)
        first_dd_text = dds[0].get_text().strip()
        self.assertIn(
            "cast-expression",
            first_dd_text,
            f"First production should be 'cast-expression', got {first_dd_text!r}",
        )

        # Remaining 3 productions should all contain multiplicative-expression and cast-expression
        operators: set[str] = set()
        for dd in dds[1:]:
            assert isinstance(dd, Tag)
            text = dd.get_text()
            self.assertIn("multiplicative-expression", text)
            self.assertIn("cast-expression", text)
            # Extract the operator (* / %)
            for op in ("*", "/", "%"):
                if op in text:
                    operators.add(op)
        self.assertEqual(
            operators,
            {"*", "/", "%"},
            f"Expected operators {{*, /, %}} in continuations, got {operators}",
        )

    def test_grammar_dd_uses_nonterminal_not_dfn(self) -> None:
        """Grammar productions must use <i class="nonterminal"> not <dfn>."""
        for dl in self.soup.find_all("dl", class_="grammar"):
            assert isinstance(dl, Tag)
            for dd in dl.find_all("dd", recursive=False):
                assert isinstance(dd, Tag)
                self.assertFalse(
                    dd.find("dfn"),
                    f"Grammar <dd> must not contain <dfn>; found in: {dd}",
                )

    def test_grammar_multi_nonterminal_production_linked(self) -> None:
        """A production with multiple nonterminals (e.g. 'unary-operator cast-expression')
        must serialize as separate <i class="nonterminal"> elements, each wrapped in
        <a href="#nonterminal-...">, not merged into a single element.
        """
        # Find the unary-expression grammar block
        dl: Optional[Tag] = next(
            (
                t
                for t in self.soup.find_all("dl", class_="grammar")
                if t.find("a", id="nonterminal-unary-expression")
            ),
            None,
        )
        self.assertIsNotNone(dl, "No grammar block for unary-expression found")
        assert dl is not None

        # Find the production that contains both unary-operator and cast-expression
        target_dd: Optional[Tag] = next(
            (
                dd
                for dd in dl.find_all("dd", recursive=False)
                if "unary-operator" in dd.get_text() and "cast-expression" in dd.get_text()
            ),
            None,
        )
        self.assertIsNotNone(
            target_dd,
            "Expected a production containing both 'unary-operator' and 'cast-expression'",
        )
        assert isinstance(target_dd, Tag)

        # Must have exactly two <i class="nonterminal"> elements (not merged into one)
        i_tags = target_dd.find_all("i", class_="nonterminal")
        self.assertEqual(
            len(i_tags),
            2,
            f"Expected 2 separate <i class='nonterminal'> elements, got {len(i_tags)}: {target_dd}",
        )
        texts = [t.get_text() for t in i_tags]
        self.assertIn("unary-operator", texts)
        self.assertIn("cast-expression", texts)

        # Each must be wrapped in <a href="#nonterminal-...">
        for i_tag in i_tags:
            assert isinstance(i_tag, Tag)
            parent_a = i_tag.parent
            assert isinstance(parent_a, Tag)
            self.assertEqual(parent_a.name, "a", f"Nonterminal {i_tag.get_text()!r} must be in <a>")
            href = str(parent_a.get("href", ""))
            self.assertTrue(
                href.startswith("#nonterminal-"),
                f"href={href!r} should start with '#nonterminal-'",
            )

    def test_main_only_has_section_children(self) -> None:
        """Every direct child of <main> must be a <section> with id='section-*'.

        This verifies the homogeneous top-level structure: no mix of raw
        content elements and section wrappers.
        """
        from bs4 import Tag as BsTag

        main = self.soup.find("main")
        self.assertIsNotNone(main, "<main> element not found")
        assert isinstance(main, BsTag)
        children = [c for c in main.children if isinstance(c, BsTag)]
        non_sections = [c for c in children if c.name != "section"]
        self.assertEqual(
            non_sections,
            [],
            f"<main> has non-<section> children: {[str(c)[:80] for c in non_sections]}",
        )
        bad_ids = [
            c.get("id", "") for c in children if not str(c.get("id", "")).startswith("section-")
        ]
        self.assertEqual(
            bad_ids,
            [],
            f"<section> elements without 'section-' id prefix: {bad_ids}",
        )

    # ------------------------------------------------------------------
    # Annex A — Language syntax summary
    # ------------------------------------------------------------------

    def test_language_syntax_summary_no_empty_sections(self) -> None:
        """Every heading in section-language-syntax-summary must be followed by
        at least one ``dl.grammar`` before the next sibling heading of the same
        or higher level (i.e. no empty sub-sections).

        The top-level A.1 (Notation) intro heading is excluded because it
        legitimately contains only a prose paragraph.
        """
        section = self.soup.find("section", id="section-language-syntax-summary")
        self.assertIsNotNone(section, "section#section-language-syntax-summary not found")
        assert isinstance(section, Tag)

        headings = section.find_all(["h2", "h3", "h4"])
        for h in headings:
            h_id = h.get("id", "")
            # Skip the A.1 / Notation intro heading (prose-only by design)
            if h_id in ("A.1", "A"):
                continue
            level = int(h.name[1])
            # Walk forward siblings until a heading of equal-or-higher level
            found_dl = False
            sibling = h.find_next_sibling()
            while sibling is not None:
                assert isinstance(sibling, Tag)
                sib_name = sibling.name
                if sib_name in ("h2", "h3", "h4") and int(sib_name[1]) <= level:
                    break
                if sibling.find("dl", class_="grammar"):
                    found_dl = True
                sibling = sibling.find_next_sibling()
            self.assertTrue(
                found_dl,
                f"Section headed by {h_id!r} contains no dl.grammar before the next sibling heading",
            )

    def test_language_syntax_summary_all_grammar(self) -> None:
        """After the intro paragraph, no plain ``<p>`` elements appear inside
        ``section-language-syntax-summary``.

        The very first ``<div class="p">`` (the "The notation is described in 6.1"
        paragraph) is intentionally skipped; all subsequent content must consist
        of grammar ``<dl>`` blocks only.
        """
        section = self.soup.find("section", id="section-language-syntax-summary")
        self.assertIsNotNone(section, "section#section-language-syntax-summary not found")
        assert isinstance(section, Tag)

        divs = section.find_all("div", class_="p")
        # The first div is the Notation intro — skip it
        for div in divs[1:]:
            assert isinstance(div, Tag)
            plain_p = div.find("p")
            self.assertIsNone(
                plain_p,
                f"Found plain <p> in section-language-syntax-summary (div id={div.get('id')!r}): "
                f"{plain_p}",
            )


class TestGrammarIndividualPage(unittest.TestCase):
    """Checks on individual-page grammar serialization (no document-level linking)."""

    soup: BeautifulSoup  # page 83: unary-expression grammar

    @classmethod
    def setUpClass(cls) -> None:
        from src.html_serializer import serialize
        from src.pdf_parser import parse_page

        pdf_path = str(PROJECT_ROOT / "n3685.pdf")
        html = serialize(parse_page(pdf_path, 99))  # page 83
        cls.soup = BeautifulSoup(html, "html.parser")

    def test_grammar_dd_uses_nonterminal_not_dfn(self) -> None:
        """Individual-page grammar <dd> must use <i class="nonterminal">, not <dfn>."""
        for dl in self.soup.find_all("dl", class_="grammar"):
            assert isinstance(dl, Tag)
            for dd in dl.find_all("dd", recursive=False):
                assert isinstance(dd, Tag)
                self.assertFalse(
                    dd.find("dfn"),
                    f"Grammar <dd> must not contain <dfn>; found in: {dd}",
                )
                # Must contain at least one nonterminal or code element
                has_content = dd.find("i", class_="nonterminal") or dd.find("code")
                self.assertTrue(has_content, f"Grammar <dd> has no recognizable content: {dd}")

    def test_grammar_multi_nonterminal_production_split(self) -> None:
        """'unary-operator cast-expression' must be two separate <i class="nonterminal">
        elements on the individual page, not merged into one <dfn> or <i>."""
        dl: Optional[Tag] = next(
            (
                t
                for t in self.soup.find_all("dl", class_="grammar")
                if "unary-expression" in (t.find("dt") or Tag(name="dt")).get_text()
            ),
            None,
        )
        self.assertIsNotNone(dl, "unary-expression grammar block not found on page 83")
        assert dl is not None

        target_dd: Optional[Tag] = next(
            (
                dd
                for dd in dl.find_all("dd", recursive=False)
                if "unary-operator" in dd.get_text() and "cast-expression" in dd.get_text()
            ),
            None,
        )
        self.assertIsNotNone(
            target_dd,
            "Expected production with 'unary-operator' and 'cast-expression' on page 83",
        )
        assert isinstance(target_dd, Tag)

        i_tags = target_dd.find_all("i", class_="nonterminal")
        self.assertEqual(
            len(i_tags),
            2,
            f"Expected 2 separate <i class='nonterminal'> for the two nonterminals, "
            f"got {len(i_tags)}: {target_dd}",
        )
        texts = [t.get_text() for t in i_tags]
        self.assertIn("unary-operator", texts)
        self.assertIn("cast-expression", texts)

    def test_toc_hrefs_resolve(self) -> None:
        """Every href referenced in the TOC must resolve to exactly one element
        in the document (no missing targets, no duplicate ids).
        """
        toc_hrefs: set[str] = set()
        for ol in self.soup.find_all("ol", class_="toc"):
            for a in ol.find_all("a"):
                href = a.get("href", "")
                if isinstance(href, str) and href.startswith("#"):
                    toc_hrefs.add(href[1:])
        for h2 in self.soup.find_all("h2", class_="toc-heading"):
            for a in h2.find_all("a"):
                href = a.get("href", "")
                if isinstance(href, str) and href.startswith("#"):
                    toc_hrefs.add(href[1:])

        missing = [sid for sid in sorted(toc_hrefs) if len(self.soup.find_all(id=sid)) == 0]
        duplicate = [
            (sid, len(self.soup.find_all(id=sid)))
            for sid in sorted(toc_hrefs)
            if len(self.soup.find_all(id=sid)) > 1
        ]

        self.assertEqual(missing, [], f"TOC hrefs with no matching element: {missing}")
        self.assertEqual(duplicate, [], f"TOC hrefs with multiple matching elements: {duplicate}")
