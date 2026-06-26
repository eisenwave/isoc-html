"""
Serialize a Page DOM to minimal HTML (no formatting whitespace).

Use prettify() to obtain an indented, human-readable version.
"""

from __future__ import annotations

from html import escape
from pathlib import Path
from typing import List

from bs4 import BeautifulSoup, Tag
from bs4.element import NavigableString

from .dom import (
    BridgeContent,
    BulletItem,
    BulletList,
    CodeNode,
    DefnList,
    DfnNode,
    Footnote,
    ForwardRefs,
    GrammarBlock,
    Heading,
    Inline,
    ISOCoverBlock,
    ItalicNode,
    LinkNode,
    MainElement,
    MathNode,
    NonterminalNode,
    OrderedList,
    Page,
    ParagraphBlock,
    ParagraphContent,
    PreBlock,
    PreToken,
    ProseBlock,
    SectionBlock,
    SupNode,
    TextNode,
    TocHeading,
    TocItem,
    TokenKind,
    VarNode,
)


def _inlines(nodes: List[Inline]) -> str:
    return "".join(_inline(n) for n in nodes)


def _inline(node: Inline) -> str:
    if isinstance(node, TextNode):
        return escape(node.text)
    elif isinstance(node, CodeNode):
        if node.kind != TokenKind.PLAIN:
            return f'<code class="tk-{node.kind.value}">{escape(node.text)}</code>'
        return f"<code>{escape(node.text)}</code>"
    elif isinstance(node, DfnNode):
        return f"<dfn>{escape(node.text)}</dfn>"
    elif isinstance(node, VarNode):
        return f"<var>{escape(node.text)}</var>"
    elif isinstance(node, SupNode):
        inner = _inlines(node.children)
        if node.id:
            return f'<sup id="{node.id}">{inner}</sup>'
        return f"<sup>{inner}</sup>"
    elif isinstance(node, LinkNode):
        inner = _inlines(node.children)
        return f'<a href="{node.href}">{inner}</a>'
    elif isinstance(node, MathNode):
        return f"<math>{node.mathml}</math>"
    elif isinstance(node, NonterminalNode):
        return f'<i class="nonterminal">{escape(node.text)}</i>'
    else:
        assert isinstance(node, ItalicNode)
        return f"<i>{escape(node.text)}</i>"


def _pre_block_html(item: PreBlock, in_synopsis: bool = False) -> str:
    """Render a PreBlock to HTML.

    When *in_synopsis* is True, function-name tokens immediately before a
    ``(`` receive a self-referential anchor ``<a id="decl-name" href="#decl-name">``
    so synopsis declarations become stable link targets.
    """
    if item.tokens:
        tokens: List[PreToken] = item.tokens
        parts: list[str] = []
        for idx, t in enumerate(tokens):
            txt = escape(t.text)
            # Decl anchor: non-whitespace FUNCTION or TYPE_GENERIC_MACRO token immediately before '('
            if (
                in_synopsis
                and t.kind in (TokenKind.FUNCTION, TokenKind.TYPE_GENERIC_MACRO)
                and t.text.strip()
            ):
                nxt = tokens[idx + 1] if idx + 1 < len(tokens) else None
                if nxt is not None and nxt.kind == TokenKind.PLAIN and nxt.text.startswith("("):
                    name = t.text.strip()
                    decl_id = f"decl-{name}"
                    prefix = t.text[: len(t.text) - len(t.text.lstrip())]
                    if prefix:
                        parts.append(escape(prefix))
                    parts.append(
                        f'<a id="{decl_id}" href="#{decl_id}"><span class="tk-{t.kind.value}">{escape(name)}</span></a>'
                    )
                    continue
            if t.kind == TokenKind.PLACEHOLDER:
                parts.append(f"<var>{txt}</var>")
            elif t.kind == TokenKind.GENERIC:
                parts.append(f'<span class="tk-generic">{txt}</span>')
            elif t.kind != TokenKind.PLAIN:
                parts.append(f'<span class="tk-{t.kind.value}">{txt}</span>')
            else:
                parts.append(txt)
        return f"<pre>{''.join(parts)}</pre>"
    return f"<pre>{escape(item.text)}</pre>"


def _para_content(item: ParagraphContent, in_synopsis: bool = False) -> str:
    if isinstance(item, ProseBlock):
        return f"<p>{_inlines(item.inlines)}</p>"
    elif isinstance(item, PreBlock):
        return _pre_block_html(item, in_synopsis)
    elif isinstance(item, BridgeContent):
        return _inlines(item.inlines)
    elif isinstance(item, BulletList):
        items_html = "".join(_bullet_item(bi) for bi in item.items)
        return f"<ul>{items_html}</ul>"
    elif isinstance(item, OrderedList):
        items_html = "".join(f"<li>{_inlines(oi.inlines)}</li>" for oi in item.items)
        return f"<ol>{items_html}</ol>"
    elif isinstance(item, DefnList):
        dl_parts = "".join(
            f"<dt>{_inlines(di.term)}</dt><dd>{_inlines(di.definition)}</dd>" for di in item.items
        )
        return f"<dl>{dl_parts}</dl>"
    else:
        assert isinstance(item, GrammarBlock)
        qualifier_suffix = f" {escape(item.qualifier)}" if item.qualifier else ""
        if item.anchor_id:
            inner = f'<a id="{item.anchor_id}" href="#{item.anchor_id}"><i class="nonterminal">{escape(item.term)}</i></a>:{qualifier_suffix}'
        else:
            inner = f'<i class="nonterminal">{escape(item.term)}</i>:{qualifier_suffix}'
        dt = f"<dt>{inner}</dt>"
        dds = "".join(f"<dd>{_inlines(prod)}</dd>" for prod in item.productions)
        return f'<dl class="grammar">{dt}{dds}</dl>'


def _bullet_item(bi: BulletItem) -> str:
    id_attr = f' id="{bi.id}"' if bi.id else ""
    content = _inlines(bi.inlines)
    if bi.nested:
        nested_items = "".join(_bullet_item(nb) for nb in bi.nested.items)
        content += f'<ul class="nested">{nested_items}</ul>'
    return f"<li{id_attr}>{content}</li>"


def _toc_item(item: TocItem) -> str:
    inner = (
        f'<a href="#{escape(item.section_id)}">{escape(item.section_id)}</a> {escape(item.title)}'
    )
    if item.children:
        inner += '<ol class="toc">' + "".join(_toc_item(c) for c in item.children) + "</ol>"
    return f"<li>{inner}</li>"


def _main_element(elem: MainElement, in_synopsis: bool = False) -> str:
    if isinstance(elem, Heading):
        lvl = elem.level
        title_html = _inlines(elem.title_inlines) if elem.title_inlines else escape(elem.title)
        if lvl <= 6:
            tag = f"h{lvl}"
            open_attrs = f"<{tag}"
        else:
            tag = "div"
            open_attrs = f'<{tag} role="heading" aria-level="{lvl}"'
        if elem.section_id and elem.subheading:
            # Back-matter heading: has an id for TOC anchors but no section-number link.
            return f'{open_attrs} id="{elem.section_id}" class="unnumbered">{title_html}</{tag}>'
        elif elem.section_id:
            return (
                f'{open_attrs} id="{elem.section_id}">'
                f'<a class="section-num" href="#{elem.section_id}">{elem.section_id}</a>'
                f" {title_html}</{tag}>"
            )
        elif elem.subheading:
            return f'{open_attrs} class="unnumbered">{title_html}</{tag}>'
        else:
            return f"{open_attrs}>{title_html}</{tag}>"
    elif isinstance(elem, ParagraphBlock):
        children_html = "".join(_para_content(c, in_synopsis) for c in elem.children)
        p_num_html = (
            f'<a class="p-num" href="#{elem.id}">{elem.num}</a>'
            if elem.id
            else f'<span class="p-num">{elem.num}</span>'
        )
        return f'<div id="{elem.id}" class="p">{p_num_html}{children_html}</div>'
    elif isinstance(elem, ForwardRefs):
        return f'<div class="forward-refs"><b>Forward references:</b>{_inlines(elem.inlines)}</div>'
    elif isinstance(elem, OrderedList):
        return _para_content(elem)
    elif isinstance(elem, BulletList):
        return _para_content(elem)
    elif isinstance(elem, DefnList):
        return _para_content(elem)
    elif isinstance(elem, ISOCoverBlock):
        cover = (
            '<div class="iso-cover">'
            "<div>INTERNATIONAL STANDARD</div>"
            "<div>\xa9ISO/IEC</div>"
            f"<div>{escape(elem.standard_ref)}</div>"
            "</div>"
        )
        if elem.title:
            cover += f'<p class="doc-title">{escape(elem.title)}</p>'
        return cover
    elif isinstance(elem, ProseBlock):
        return f"<p>{_inlines(elem.inlines)}</p>"
    elif isinstance(elem, PreBlock):
        return _pre_block_html(elem, in_synopsis)
    elif isinstance(elem, GrammarBlock):
        return _para_content(elem)
    elif isinstance(elem, TocHeading):
        sid = escape(elem.section_id)
        return f'<h2 class="toc-heading"><a href="#{sid}">{sid}</a> {escape(elem.title)}</h2>'
    else:
        items_html = "".join(_toc_item(i) for i in elem.items)
        return f'<ol class="toc">{items_html}</ol>'


def _serialize_main(elements: List[MainElement]) -> str:
    """Serialize a list of MainElements, injecting declaration anchors into
    PreBlocks that immediately follow a ``Synopsis`` heading.
    """
    parts: list[str] = []
    synopsis_pending = False
    for elem in elements:
        if isinstance(elem, Heading) and elem.subheading and elem.title.strip() == "Synopsis":
            synopsis_pending = True
            parts.append(_main_element(elem))
        elif isinstance(elem, ParagraphBlock) and synopsis_pending:
            parts.append(_main_element(elem, in_synopsis=True))
            synopsis_pending = False
        else:
            synopsis_pending = False
            parts.append(_main_element(elem))
    return "".join(parts)


def serialize(page: Page) -> str:
    """Serialize a Page to a minimal HTML string (no added whitespace)."""
    header_html = (
        f"<header><b>{escape(page.header.bold_text)}</b>{escape(page.header.rest_text)}</header>"
    )

    main_html = f"<main>{_serialize_main(page.main)}</main>"

    footnotes_html = ""
    if page.footnotes:
        fn_parts = "".join(f'<p id="{fn.id}">{_inlines(fn.inlines)}</p>' for fn in page.footnotes)
        footnotes_html = f'<div class="footnotes">{fn_parts}</div>'

    footer_html = ""
    if page.footer:
        f = page.footer
        footer_html = (
            f"<footer>"
            f'<span class="current-section">{escape(f.current_section)}</span>'
            f'<span class="copyright">{escape(f.copyright)}</span>'
            f'<span class="current-clause">{escape(f.current_clause)}</span>'
            f' &#8212; <span class="page">{f.page}</span>'
            f"</footer>"
        )

    body = header_html + main_html + footnotes_html + footer_html
    return f"<html><head></head><body>{body}</body></html>"


def _draft_notice_html(draft_id: str) -> str:
    """Return a disclaimer <header> element for the given working-draft ID."""
    url = f"https://www.open-std.org/jtc1/sc22/wg14/www/docs/{draft_id.lower()}.pdf"
    eurl = escape(url)
    eid = escape(draft_id)
    return (
        f'<header class="draft-notice">'
        f"<p>"
        f'This document was generated from the <a href="{eurl}">{eid} working draft</a>'
        f" of ISO/IEC\xa09899."
        f" <strong>This is not an official ISO publication.</strong>"
        f" The HTML conversion is automated and ridden with errors."
        f' For an accurate version of the standard, refer to the <a href="{eurl}">PDF</a>.'
        f"</p>"
        f"</header>"
    )


def serialize_document(
    sections: List[SectionBlock],
    footnotes: List[Footnote],
    draft_id: str = "",
) -> str:
    """Serialize a full merged document to a minimal HTML string.

    Unlike ``serialize()``, this omits per-page headers and footers and emits
    a proper ``<!DOCTYPE html>`` declaration.  Content is grouped into
    ``<section>`` elements whose ``id`` matches ``section-{slug}``.
    Footnotes are placed in a synthesised ``<section id="section-footnotes">``.
    A ``<header class="draft-notice">`` is prepended when *draft_id* is given.
    """
    _css = (Path(__file__).parent / "style.css").read_text(encoding="utf-8")
    style_tag = f"<style>\n{_css}</style>"

    body_parts: List[str] = []
    for section in sections:
        inner = _serialize_main(section.elements)
        body_parts.append(f'<section id="section-{escape(section.slug)}">{inner}</section>')

    if footnotes:
        fn_parts = "".join(f'<p id="{fn.id}">{_inlines(fn.inlines)}</p>' for fn in footnotes)
        body_parts.append(
            f'<section id="section-footnotes"><div class="footnotes">{fn_parts}</div></section>'
        )

    notice = _draft_notice_html(draft_id) if draft_id else ""
    body = f"{notice}<main>{''.join(body_parts)}</main>"
    return f'<!DOCTYPE html><html><head><meta charset="utf-8"/>{style_tag}</head><body>{body}</body></html>'


def prettify(html: str) -> str:
    """Return a human-readable, 2-space-indented HTML string.

    Inline elements are never placed on their own lines, so browsers do not
    insert unwanted whitespace between them and adjacent text/punctuation.
    """
    soup = BeautifulSoup(html, "html.parser")
    root = soup.find("html")
    if root is None:
        return html
    return _fmt_block(root, 0, "  ") + "\n"


# Tags treated as inline (rendered as a flat string, never on their own line).
_INLINE = frozenset({"a", "b", "code", "dfn", "i", "math", "var", "sup", "span"})


def _fmt_inline(node: object) -> str:
    """Render a node and all its descendants as a flat string with no added whitespace."""
    if isinstance(node, NavigableString):
        return escape(str(node))
    if isinstance(node, Tag):
        attrs = "".join(
            f' {k}="{" ".join(v) if isinstance(v, list) else v}"' for k, v in node.attrs.items()
        )
        content = "".join(_fmt_inline(c) for c in node.children)
        return f"<{node.name}{attrs}>{content}</{node.name}>"
    return ""


def _fmt_block(node: object, level: int, ind: str) -> str:
    """Format a node with block-level indentation.

    Inline nodes are rendered flat (via _fmt_inline).  Block nodes have each
    child on its own indented line, except when all children are inline — in
    that case the entire content is placed on a single indented line so that
    no whitespace is inserted between inline elements and adjacent text.
    """
    if isinstance(node, NavigableString):
        text = escape(str(node)).strip()
        return (ind * level + text) if text else ""
    if not isinstance(node, Tag):
        return ""
    if node.name in _INLINE:
        return ind * level + _fmt_inline(node)

    pfx = ind * level
    cpfx = ind * (level + 1)

    # <pre> content is preformatted — never re-indent the inner text.
    # Use _fmt_inline on each child so that <var> tags are preserved and
    # raw < / > characters in NavigableStrings are properly re-escaped.
    if node.name == "pre":
        inner = "".join(_fmt_inline(c) for c in node.children)
        stripped = inner.strip()
        if "\n" in stripped:
            return f"{pfx}<pre>\n{stripped}\n{pfx}</pre>"
        return f"{pfx}<pre>{stripped}</pre>"

    attrs = "".join(
        f' {k}="{" ".join(v) if isinstance(v, list) else v}"' for k, v in node.attrs.items()
    )

    # Drop pure-whitespace text nodes that are block-level indentation artifacts,
    # but preserve them when inside an all-inline context (spaces between dfn/code/etc).
    children = list(node.children)

    if not children:
        return f"{pfx}<{node.name}{attrs}></{node.name}>"

    # All-inline content → render on a single indented line.
    if all(
        isinstance(c, NavigableString) or (isinstance(c, Tag) and c.name in _INLINE)
        for c in children
    ):
        content = "".join(_fmt_inline(c) for c in children)
        if not content.strip():
            return f"{pfx}<{node.name}{attrs}></{node.name}>"
        return f"{pfx}<{node.name}{attrs}>\n{cpfx}{content.strip()}\n{pfx}</{node.name}>"

    # Mixed/block children → each child on its own line.
    lines = [_fmt_block(c, level + 1, ind) for c in children]
    inner = "\n".join(ln for ln in lines if ln)
    return f"{pfx}<{node.name}{attrs}>\n{inner}\n{pfx}</{node.name}>"
