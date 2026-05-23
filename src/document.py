# pyright: basic
"""Build a full-document DOM by streaming through all pages of a PDF.

The PDF is opened exactly once.  Pages are parsed in order and their
main-content elements are appended to a single list, with cross-page
continuations merged on the fly:

* Adjacent ``<ol>`` / ``<ul>`` / ``<dl>`` lists → items merged.
* A ``<pre>`` block that spills across a page boundary → text concatenated.

Footnotes are collected from every page into one list that callers can
place at the end of the document.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

import fitz  # type: ignore[import]

from .dom import (
    BridgeContent,
    BulletList,
    DefnList,
    DfnNode,
    Footnote,
    GrammarBlock,
    Inline,
    LinkNode,
    MainElement,
    NonterminalNode,
    OrderedList,
    ParagraphBlock,
    PreBlock,
    ProseBlock,
    SectionBlock,
)
from .pdf_parser import parse_page_from_doc


def _clause_to_slug(clause: str) -> str:
    """Convert a PDF clause name to a URL-safe slug.

    Examples::

        'Abstract'                               -> 'abstract'
        'Language syntax summary'                -> 'language-syntax-summary'
        'ISO/IEC 60559 floating-point arithmetic' -> 'iso-iec-60559-floating-point-arithmetic'
    """
    slug = clause.lower()
    slug = re.sub(r"[^a-z0-9]+", "-", slug)
    return slug.strip("-")


@dataclass
class Document:
    """Full-document DOM: top-level sections and collected footnotes."""

    sections: List[SectionBlock] = field(default_factory=list)
    footnotes: List[Footnote] = field(default_factory=list)
    draft_id: str = ""  # e.g. "N3685", extracted from PDF header text


def parse_document(pdf_path: str) -> Document:
    """Open *pdf_path* once and parse every page into a single Document.

    Pages are streamed in index order (0 … page_count−1).  Elements are
    grouped into ``SectionBlock``s by the ``current_clause`` value from
    each page's footer.  Cross-page list and code-block continuations are
    merged within sections so the returned DOM has no page seams.
    """
    doc: Any = fitz.open(pdf_path)
    sections: List[SectionBlock] = []
    footnotes: List[Footnote] = []
    current_section: Optional[SectionBlock] = None
    current_clause: str = ""

    # Extract working-draft identifier from the first page (e.g. "N3685").
    draft_id = ""
    if doc.page_count > 0:
        first_text: str = doc[0].get_text("text")
        m = re.search(r"(N\d+)\s+working\s+draft", first_text, re.IGNORECASE)
        if m:
            draft_id = m.group(1).upper()

    for idx in range(doc.page_count):
        page = parse_page_from_doc(doc, idx)

        # Determine clause for this page; inherit previous when footer absent.
        clause = page.footer.current_clause if page.footer else ""
        if not clause:
            clause = current_clause

        # Start a new section when the clause changes.
        if clause != current_clause or current_section is None:
            slug = _clause_to_slug(clause) if clause else "abstract"
            current_section = SectionBlock(slug=slug)
            sections.append(current_section)
            current_clause = clause

        for elem in page.main:
            _append_main(current_section.elements, elem)
        footnotes.extend(page.footnotes)

    # Grammar linking operates on a flat element list; shared references
    # mean mutations propagate back into each SectionBlock automatically.
    all_elements: List[MainElement] = [e for s in sections for e in s.elements]
    _apply_grammar_linking(all_elements, footnotes)
    return Document(sections=sections, footnotes=footnotes, draft_id=draft_id)


# ---------------------------------------------------------------------------
# Cross-page merge helpers
# ---------------------------------------------------------------------------


def _append_main(main: List[MainElement], elem: MainElement) -> None:
    """Append *elem* to *main*, merging with the last element when appropriate."""
    if not main:
        main.append(elem)
        return

    prev = main[-1]

    # Merge consecutive homogeneous list types.
    if isinstance(prev, OrderedList) and isinstance(elem, OrderedList):
        # If the first item on the new page is a continuation of the last item on the
        # previous page (split by a page boundary mid-sentence), extend that item's
        # inlines instead of appending a new list item.
        if elem.items and elem.items[0].is_continuation and prev.items:
            prev.items[-1].inlines.extend(elem.items[0].inlines)
            prev.items.extend(elem.items[1:])
        else:
            prev.items.extend(elem.items)
        return

    if isinstance(prev, BulletList) and isinstance(elem, BulletList):
        prev.items.extend(elem.items)
        return

    if isinstance(prev, DefnList) and isinstance(elem, DefnList):
        prev.items.extend(elem.items)
        return

    # Merge an orphan list that continues a list at the tail of the previous
    # ParagraphBlock (the list crossed a page boundary inside the paragraph).
    if isinstance(prev, ParagraphBlock) and prev.children:
        last = prev.children[-1]
        if isinstance(last, OrderedList) and isinstance(elem, OrderedList):
            if elem.items and elem.items[0].is_continuation and last.items:
                last.items[-1].inlines.extend(elem.items[0].inlines)
                last.items.extend(elem.items[1:])
            else:
                last.items.extend(elem.items)
            return
        if isinstance(last, BulletList) and isinstance(elem, BulletList):
            last.items.extend(elem.items)
            return
        if isinstance(last, DefnList) and isinstance(elem, DefnList):
            last.items.extend(elem.items)
            return

    # Merge a GrammarBlock that spills across a page boundary: the continuation
    # page emits a ParagraphBlock("", 0, [GrammarBlock("", productions)]).
    # Append the orphan productions to the trailing GrammarBlock on the previous page.
    if isinstance(prev, ParagraphBlock) and isinstance(elem, ParagraphBlock):
        if (
            prev.children
            and isinstance(prev.children[-1], GrammarBlock)
            and elem.children
            and isinstance(elem.children[0], GrammarBlock)
            and not elem.children[0].term  # orphan continuation has empty term
        ):
            prev.children[-1].productions.extend(elem.children[0].productions)
            prev.children.extend(elem.children[1:])
            return

    # Merge a PreBlock that spills across a page boundary into the previous
    # ParagraphBlock's trailing PreBlock.
    if isinstance(prev, ParagraphBlock) and isinstance(elem, ParagraphBlock):
        if (
            prev.children
            and isinstance(prev.children[-1], PreBlock)
            and elem.children
            and isinstance(elem.children[0], PreBlock)
        ):
            prev_pre = prev.children[-1]
            elem_pre = elem.children[0]
            prev_pre.text += "\n" + elem_pre.text
            if prev_pre.tokens is not None and elem_pre.tokens is not None:
                from .dom import PreToken  # noqa: PLC0415

                prev_pre.tokens.append(PreToken("\n"))
                prev_pre.tokens.extend(elem_pre.tokens)
            elif elem_pre.tokens is not None:
                prev_pre.tokens = None  # discard partial tokens; text is still correct
            prev.children.extend(elem.children[1:])
            return

    main.append(elem)


# ---------------------------------------------------------------------------
# Grammar linking post-processing
# ---------------------------------------------------------------------------


def _replace_dfn_in_inlines(inlines: List[Inline], grammar_terms: Dict[str, str]) -> List[Inline]:
    """Replace DfnNode/NonterminalNode with a linked NonterminalNode when text is a grammar term."""
    result: List[Inline] = []
    for node in inlines:
        if isinstance(node, (DfnNode, NonterminalNode)) and node.text in grammar_terms:
            nid = grammar_terms[node.text]
            result.append(LinkNode(f"{nid}", [NonterminalNode(node.text)]))
        elif isinstance(node, LinkNode):
            result.append(
                LinkNode(node.href, _replace_dfn_in_inlines(node.children, grammar_terms))
            )
        else:
            result.append(node)
    return result


def _apply_grammar_linking(main: List[MainElement], footnotes: List[Footnote]) -> None:
    """Post-processing: collect grammar terms and link nonterminals throughout the DOM.

    Pass 1: collect all GrammarBlock terms; mark the first GrammarBlock for
    each term with an anchor_id.
    Pass 2: replace DfnNode(text) with LinkNode(NonterminalNode(text)) when
    the text matches a known grammar term.
    """
    grammar_terms: Dict[str, str] = {}  # term -> "#nonterminal-{term}"

    # Pass 1: collect terms
    for elem in main:
        if not isinstance(elem, ParagraphBlock):
            continue
        for child in elem.children:
            if isinstance(child, GrammarBlock) and child.term not in grammar_terms:
                nonterminal_id = "nonterminal-" + child.term
                grammar_terms[child.term] = f"#{nonterminal_id}"
                child.anchor_id = nonterminal_id

    if not grammar_terms:
        return

    # Pass 2: replace DfnNodes in grammar productions and throughout the DOM
    for elem in main:
        if not isinstance(elem, ParagraphBlock):
            continue
        for child in elem.children:
            if isinstance(child, GrammarBlock):
                child.productions = [
                    _replace_dfn_in_inlines(prod, grammar_terms) for prod in child.productions
                ]
            elif isinstance(child, ProseBlock):
                child.inlines = _replace_dfn_in_inlines(child.inlines, grammar_terms)
            elif isinstance(child, BridgeContent):
                child.inlines = _replace_dfn_in_inlines(child.inlines, grammar_terms)
            elif isinstance(child, BulletList):
                for item in child.items:
                    item.inlines = _replace_dfn_in_inlines(item.inlines, grammar_terms)
            elif isinstance(child, OrderedList):
                for item in child.items:
                    item.inlines = _replace_dfn_in_inlines(item.inlines, grammar_terms)
