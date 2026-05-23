"""
Deserialize a Page DOM from an HTML file (golden-file format).
"""

from __future__ import annotations

import re
from typing import Any, List, Optional, cast

from bs4 import BeautifulSoup, Tag
from bs4.element import NavigableString, PageElement

from .dom import (
    BridgeContent,
    BulletItem,
    BulletList,
    CodeNode,
    DefnItem,
    DefnList,
    DfnNode,
    Footer,
    Footnote,
    ForwardRefs,
    GrammarBlock,
    Header,
    Heading,
    Inline,
    ISOCoverBlock,
    ItalicNode,
    LinkNode,
    MainElement,
    MathNode,
    NonterminalNode,
    OrderedItem,
    OrderedList,
    Page,
    ParagraphBlock,
    ParagraphContent,
    PreBlock,
    ProseBlock,
    SupNode,
    TextNode,
    TocHeading,
    TocItem,
    TocList,
    VarNode,
)

_SECTION_ID_RE = re.compile(r"^\d+(\.\d+)*$")


# ---------------------------------------------------------------------------
# Inline parsing
# ---------------------------------------------------------------------------


def _parse_inline_node(node: PageElement) -> Optional[Inline]:
    """Convert a single BS4 node to an Inline, or None if it should be skipped."""
    if isinstance(node, NavigableString):
        text = str(node)
        if text:
            return TextNode(text)
        return None
    if isinstance(node, Tag):
        if node.name == "code":
            return CodeNode(node.get_text())
        elif node.name == "dfn":
            return DfnNode(node.get_text())
        elif node.name == "var":
            return VarNode(node.get_text())
        elif node.name == "sup":
            _raw_id = node.get("id")
            sup_id: Optional[str] = _raw_id if isinstance(_raw_id, str) else None
            children = _parse_inlines(node)
            return SupNode(sup_id, children)
        elif node.name == "a":
            _raw_href = node.get("href", "")
            href: str = _raw_href if isinstance(_raw_href, str) else ""
            children = _parse_inlines(node)
            return LinkNode(href, children)
        elif node.name == "i":
            classes_i: list[str] = cast(list[str], node.get("class") or [])
            if "nonterminal" in classes_i:
                return NonterminalNode(node.get_text())
            return ItalicNode(node.get_text())
        elif node.name == "math":
            return MathNode(node.decode_contents())
    return None


def _parse_inlines(node: Tag) -> List[Inline]:
    """Parse all inline children of a BS4 element."""
    result: List[Inline] = []
    for child in node.children:
        inline = _parse_inline_node(child)
        if inline is not None:
            result.append(inline)
    return result


# ---------------------------------------------------------------------------
# Paragraph content parsing
# ---------------------------------------------------------------------------


def _parse_paragraph_div(div: Tag) -> tuple[int, List[ParagraphContent]]:
    """
    Parse a <div class="p"> element.
    Returns (paragraph_num, children).
    """
    para_num = 1
    children: List[ParagraphContent] = []
    bridge_buf: List[Inline] = []

    def flush_bridge():
        nonlocal bridge_buf
        if bridge_buf:
            children.append(BridgeContent(list(bridge_buf)))
            bridge_buf = []

    for child in div.children:
        if isinstance(child, NavigableString):
            text = str(child)
            if text.strip():
                bridge_buf.append(TextNode(text))
            elif bridge_buf:
                # preserve whitespace-only separators inside bridge
                bridge_buf.append(TextNode(text))
        elif isinstance(child, Tag):
            if child.name in ("span", "a") and "p-num" in (child.get("class") or []):
                para_num = int(child.get_text().strip())
                continue
            elif child.name == "p":
                flush_bridge()
                children.append(ProseBlock(_parse_inlines(child)))
            elif child.name == "pre":
                flush_bridge()
                children.append(PreBlock(child.get_text()))
            elif child.name == "ul":
                flush_bridge()
                items: List[BulletItem] = []
                for li in child.find_all("li", recursive=False):
                    _raw_item_id = li.get("id")
                    item_id: Optional[str] = _raw_item_id if isinstance(_raw_item_id, str) else None
                    items.append(BulletItem(item_id, _parse_inlines(li)))
                children.append(BulletList(items))
            elif child.name == "ol":
                flush_bridge()
                items_ol: List[OrderedItem] = []
                for li in child.find_all("li", recursive=False):
                    items_ol.append(OrderedItem(_parse_inlines(li)))
                children.append(OrderedList(items_ol))
            elif child.name == "dl":
                flush_bridge()
                dl_classes: list[str] = cast(list[str], child.get("class") or [])
                if "grammar" in dl_classes:
                    dt_el = child.find("dt")
                    term = ""
                    qualifier = ""
                    if dt_el:
                        i_el = dt_el.find("i", class_="nonterminal")
                        if i_el:
                            term = i_el.get_text().strip()
                        # qualifier: text after the colon in the dt
                        dt_text = dt_el.get_text()
                        colon_pos = dt_text.find(":")
                        if colon_pos >= 0:
                            qualifier = dt_text[colon_pos + 1 :].strip()
                    productions: list[list[Inline]] = []
                    for dd_el in child.find_all("dd", recursive=False):
                        productions.append(_parse_inlines(dd_el))
                    children.append(GrammarBlock(term, productions, qualifier=qualifier))
                else:
                    items_dl: List[DefnItem] = []
                    pending_term: List[Inline] = []
                    for el in child.children:
                        if not isinstance(el, Tag):
                            continue
                        if el.name == "dt":
                            pending_term = _parse_inlines(el)
                        elif el.name == "dd":
                            items_dl.append(DefnItem(pending_term, _parse_inlines(el)))
                            pending_term = []
                    if items_dl:
                        children.append(DefnList(items_dl))
            else:
                # Inline tag (code, dfn, a, sup, b, …) directly in the div
                inline = _parse_inline_node(child)
                if inline is not None:
                    bridge_buf.append(inline)

    flush_bridge()
    return para_num, children


# ---------------------------------------------------------------------------
# TOC list parsing
# ---------------------------------------------------------------------------


def _parse_toc_list(ol: Tag) -> List[TocItem]:
    """Recursively parse an <ol class="toc"> into TocItem objects."""
    items: List[TocItem] = []
    for li in ol.find_all("li", recursive=False):
        a_tag = li.find("a")
        section_id = a_tag.get_text().strip() if a_tag else ""
        # Title: text content after the <a>, before any nested <ol>
        title_parts: list[str] = []
        found_a = False
        for c in li.children:
            if isinstance(c, Tag) and c.name == "a":
                found_a = True
                continue
            if isinstance(c, Tag) and c.name == "ol":
                break
            if found_a:
                if isinstance(c, NavigableString):
                    title_parts.append(str(c))
                else:
                    title_parts.append(c.get_text())
        title = "".join(title_parts).strip()
        nested_ol = li.find("ol", recursive=False)
        children: list[TocItem] = _parse_toc_list(nested_ol) if isinstance(nested_ol, Tag) else []
        items.append(TocItem(section_id, title, children))
    return items


# ---------------------------------------------------------------------------
# Main element parsing
# ---------------------------------------------------------------------------


def _parse_main(main_tag: Tag) -> List[MainElement]:
    elements: List[MainElement] = []
    for child in main_tag.children:
        if not isinstance(child, Tag):
            continue
        tag = child.name
        classes_raw: Any = child.get("class") or []
        classes: list[str] = cast(list[str], classes_raw)

        # Headings h1–h6, or <div role="heading" aria-level="N"> for N > 6
        _aria_level_raw = child.get("aria-level") if tag == "div" else None
        _is_aria_heading = (
            tag == "div" and child.get("role") == "heading" and _aria_level_raw is not None
        )
        if tag in ("h1", "h2", "h3", "h4", "h5", "h6") or _is_aria_heading:
            # TocHeading: <h2 class="toc-heading"><a href="#1">1</a> Scope</h2>
            if "toc-heading" in classes:
                a_tag = child.find("a")
                section_id = a_tag.get_text().strip() if a_tag else ""
                title_parts: list[str] = []
                found_a = False
                for c in child.children:
                    if isinstance(c, Tag) and c.name == "a":
                        found_a = True
                        continue
                    if found_a:
                        if isinstance(c, NavigableString):
                            title_parts.append(str(c))
                        else:
                            title_parts.append(c.get_text())
                title = "".join(title_parts).strip()
                elements.append(TocHeading(section_id, title))
            else:
                _raw_sid = child.get("id", "")
                section_id: str = _raw_sid if isinstance(_raw_sid, str) else ""
                if _is_aria_heading:
                    level_from_tag = int(str(_aria_level_raw))
                else:
                    level_from_tag = int(tag[1])
                # Title is everything after the section-num element (span or a)
                title_parts_h: list[str] = []
                for c in child.children:
                    if isinstance(c, Tag) and "section-num" in (c.get("class") or []):
                        continue
                    title_parts_h.append(str(c) if isinstance(c, NavigableString) else c.get_text())
                title = "".join(title_parts_h).strip()
                explicit_level = level_from_tag if not section_id else None
                elements.append(
                    Heading(section_id, title, explicit_level, subheading="unnumbered" in classes)
                )

        elif tag == "p":
            classes_p = cast(list[str], child.get("class") or [])
            if "doc-title" in classes_p:
                # Attach the document title back to the preceding ISOCoverBlock.
                if elements and isinstance(elements[-1], ISOCoverBlock):
                    elements[-1].title = child.get_text().strip()
                # else: ignore (no preceding cover block to attach to)
            else:
                elements.append(ProseBlock(_parse_inlines(child)))

        elif tag == "ol":
            if "toc" in classes:
                items = _parse_toc_list(child)
                elements.append(TocList(items))
            else:
                items_ol: List[OrderedItem] = []
                for li in child.find_all("li", recursive=False):
                    items_ol.append(OrderedItem(_parse_inlines(li)))
                elements.append(OrderedList(items_ol))

        elif tag == "ul":
            items_ul: List[BulletItem] = []
            for li in child.find_all("li", recursive=False):
                _raw_li_id = li.get("id")
                _li_id: Optional[str] = _raw_li_id if isinstance(_raw_li_id, str) else None
                items_ul.append(BulletItem(_li_id, _parse_inlines(li)))
            elements.append(BulletList(items_ul))

        elif tag == "dl":
            items_dl2: List[DefnItem] = []
            pending_term2: List[Inline] = []
            for el in child.children:
                if not isinstance(el, Tag):
                    continue
                if el.name == "dt":
                    pending_term2 = _parse_inlines(el)
                elif el.name == "dd":
                    items_dl2.append(DefnItem(pending_term2, _parse_inlines(el)))
                    pending_term2 = []
            if items_dl2:
                elements.append(DefnList(items_dl2))

        elif tag == "div":
            if "iso-cover" in classes:
                divs = child.find_all("div", recursive=False)
                standard_ref = divs[2].get_text().strip() if len(divs) > 2 else ""
                elements.append(ISOCoverBlock(standard_ref, ""))
            elif "p" in classes:
                _raw_div_id = child.get("id", "")
                div_id: str = _raw_div_id if isinstance(_raw_div_id, str) else ""
                para_num, para_children = _parse_paragraph_div(child)
                elements.append(ParagraphBlock(div_id, para_num, para_children))
            elif "forward-refs" in classes:
                # Skip the <b>Forward references:</b> node, parse the rest
                inlines: List[Inline] = []
                skip_next_whitespace = True
                for c in child.children:
                    if isinstance(c, Tag) and c.name == "b":
                        skip_next_whitespace = True
                        continue
                    inline = _parse_inline_node(c)
                    if inline is not None:
                        if skip_next_whitespace and isinstance(inline, TextNode):
                            # strip leading whitespace/newline after the <b>
                            stripped = inline.text.lstrip()
                            skip_next_whitespace = False
                            if stripped:
                                inlines.append(TextNode(stripped))
                        else:
                            skip_next_whitespace = False
                            inlines.append(inline)
                elements.append(ForwardRefs(inlines))
    return elements


# ---------------------------------------------------------------------------
# Footnote parsing
# ---------------------------------------------------------------------------


def _parse_footnotes(div: Tag) -> List[Footnote]:
    footnotes: List[Footnote] = []
    for p in div.find_all("p", recursive=False):
        _raw_fn_id = p.get("id", "")
        fn_id: str = _raw_fn_id if isinstance(_raw_fn_id, str) else ""
        inlines = _parse_inlines(p)
        footnotes.append(Footnote(fn_id, inlines))
    return footnotes


# ---------------------------------------------------------------------------
# Header / Footer parsing
# ---------------------------------------------------------------------------


def _parse_header(header: Tag) -> Header:
    bold_text = ""
    rest_text = ""
    for child in header.children:
        if isinstance(child, Tag) and child.name == "b":
            bold_text = child.get_text()
        elif isinstance(child, NavigableString):
            rest_text += str(child)
        elif isinstance(child, Tag):
            rest_text += child.get_text()
    return Header(bold_text, rest_text)


def _parse_footer(footer: Tag) -> Footer:
    current_section = ""
    copyright_text = ""
    current_clause = ""
    page: str = ""
    for child in footer.children:
        if isinstance(child, Tag) and child.name == "span":
            classes_raw2: Any = child.get("class") or []
            classes: list[str] = cast(list[str], classes_raw2)
            text = child.get_text()
            if "current-section" in classes:
                current_section = text
            elif "copyright" in classes:
                copyright_text = text
            elif "current-clause" in classes:
                current_clause = text
            elif "page" in classes:
                page = text.strip()
    return Footer(current_section, copyright_text, current_clause, page)


# ---------------------------------------------------------------------------
# Top-level parse function
# ---------------------------------------------------------------------------


def parse_html(html_text: str) -> Page:
    """Parse an HTML golden file into a Page DOM object."""
    soup = BeautifulSoup(html_text, "lxml")
    body = soup.find("body")
    if not isinstance(body, Tag):
        return Page(Header("", ""), [], [], None)

    header_tag = body.find("header")
    main_tag = body.find("main")
    footnotes_div = body.find("div", class_="footnotes")
    footer_tag = body.find("footer")

    header = _parse_header(header_tag) if header_tag else Header("", "")
    main_elements = _parse_main(main_tag) if main_tag else []
    footnotes = _parse_footnotes(footnotes_div) if footnotes_div else []
    footer = _parse_footer(footer_tag) if footer_tag else None

    return Page(header, main_elements, footnotes, footer)
