"""
Normalize DOM objects for comparison.
Merges consecutive TextNodes, collapses internal whitespace, and strips
leading/trailing whitespace from text nodes at inline boundaries.
"""

from __future__ import annotations

import re
from typing import List

from .dom import (
    BridgeContent,
    BulletItem,
    BulletList,
    CodeNode,
    DefnItem,
    DefnList,
    DfnNode,
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


def _norm_text(t: str) -> str:
    """Collapse whitespace and strip."""
    return re.sub(r"\s+", " ", t).strip()


def norm_inlines(inlines: List[Inline]) -> List[Inline]:
    """Normalize a list of inlines: merge consecutive TextNodes and collapse whitespace."""
    # First pass: normalize children recursively
    expanded: List[Inline] = []
    for node in inlines:
        if isinstance(node, TextNode):
            expanded.append(node)
        elif isinstance(node, CodeNode):
            expanded.append(CodeNode(node.text))
        elif isinstance(node, DfnNode):
            expanded.append(DfnNode(node.text))
        elif isinstance(node, VarNode):
            expanded.append(VarNode(node.text))
        elif isinstance(node, ItalicNode):
            expanded.append(ItalicNode(node.text))
        elif isinstance(node, MathNode):
            expanded.append(MathNode(node.mathml))
        elif isinstance(node, SupNode):
            expanded.append(SupNode(node.id, norm_inlines(node.children)))
        elif isinstance(node, NonterminalNode):
            expanded.append(NonterminalNode(node.text))
        else:
            assert isinstance(node, LinkNode)
            expanded.append(LinkNode(node.href, norm_inlines(node.children)))

    # Second pass: merge consecutive TextNodes; strip leading/trailing whitespace
    # from DfnNode (and CodeNode) into the surrounding text stream.
    merged: List[Inline] = []
    text_buf = ""
    for node in expanded:
        if isinstance(node, TextNode):
            text_buf += node.text
        elif isinstance(node, DfnNode):
            # Move any leading/trailing whitespace out of the DfnNode
            t = node.text
            leading = t[: len(t) - len(t.lstrip())]
            trailing = t[len(t.rstrip()) :]
            stripped = t.strip()
            if leading:
                text_buf += leading
            if text_buf:
                norm = _norm_text(text_buf)
                if norm:
                    merged.append(TextNode(norm))
                text_buf = ""
            if stripped:
                merged.append(DfnNode(stripped))
            if trailing:
                text_buf += trailing
        elif isinstance(node, VarNode):
            # Same whitespace treatment as DfnNode
            t = node.text
            leading = t[: len(t) - len(t.lstrip())]
            trailing = t[len(t.rstrip()) :]
            stripped = t.strip()
            if leading:
                text_buf += leading
            if text_buf:
                norm = _norm_text(text_buf)
                if norm:
                    merged.append(TextNode(norm))
                text_buf = ""
            if stripped:
                merged.append(VarNode(stripped))
            if trailing:
                text_buf += trailing
        elif isinstance(node, ItalicNode):
            # Same whitespace treatment as DfnNode
            t = node.text
            leading = t[: len(t) - len(t.lstrip())]
            trailing = t[len(t.rstrip()) :]
            stripped = t.strip()
            if leading:
                text_buf += leading
            if text_buf:
                norm = _norm_text(text_buf)
                if norm:
                    merged.append(TextNode(norm))
                text_buf = ""
            if stripped:
                merged.append(ItalicNode(stripped))
            if trailing:
                text_buf += trailing
        elif isinstance(node, NonterminalNode):
            if text_buf:
                t = _norm_text(text_buf)
                if t:
                    merged.append(TextNode(t))
                text_buf = ""
            merged.append(NonterminalNode(node.text.strip()))
        else:
            if text_buf:
                t = _norm_text(text_buf)
                if t:
                    merged.append(TextNode(t))
                text_buf = ""
            merged.append(node)
    if text_buf:
        t = _norm_text(text_buf)
        if t:
            merged.append(TextNode(t))

    return merged


def norm_para_content(item: ParagraphContent) -> ParagraphContent:
    if isinstance(item, ProseBlock):
        return ProseBlock(norm_inlines(item.inlines))
    elif isinstance(item, PreBlock):
        return PreBlock(item.text.strip())
    elif isinstance(item, BridgeContent):
        return BridgeContent(norm_inlines(item.inlines))
    elif isinstance(item, BulletList):
        return BulletList([BulletItem(bi.id, norm_inlines(bi.inlines)) for bi in item.items])
    elif isinstance(item, OrderedList):
        return OrderedList([OrderedItem(norm_inlines(oi.inlines)) for oi in item.items])
    elif isinstance(item, DefnList):
        return DefnList(
            [DefnItem(norm_inlines(di.term), norm_inlines(di.definition)) for di in item.items]
        )
    else:
        assert isinstance(item, GrammarBlock)
        return GrammarBlock(
            item.term.strip(),
            [norm_inlines(prod) for prod in item.productions],
            qualifier=item.qualifier.strip(),
        )


def _norm_toc_items(items: List[TocItem]) -> List[TocItem]:
    return [
        TocItem(i.section_id.strip(), i.title.strip(), _norm_toc_items(i.children)) for i in items
    ]


def norm_main_element(elem: MainElement) -> MainElement:
    if isinstance(elem, Heading):
        return Heading(
            elem.section_id, elem.title.strip(), elem.explicit_level, subheading=elem.subheading
        )
    elif isinstance(elem, ParagraphBlock):
        return ParagraphBlock(
            elem.id,
            elem.num,
            [norm_para_content(c) for c in elem.children],
        )
    elif isinstance(elem, ForwardRefs):
        return ForwardRefs(norm_inlines(elem.inlines))
    elif isinstance(elem, OrderedList):
        return OrderedList([OrderedItem(norm_inlines(oi.inlines)) for oi in elem.items])
    elif isinstance(elem, BulletList):
        return BulletList([BulletItem(bi.id, norm_inlines(bi.inlines)) for bi in elem.items])
    elif isinstance(elem, DefnList):
        return DefnList(
            [DefnItem(norm_inlines(di.term), norm_inlines(di.definition)) for di in elem.items]
        )
    elif isinstance(elem, ISOCoverBlock):
        return ISOCoverBlock(elem.standard_ref.strip(), elem.title.strip())
    elif isinstance(elem, ProseBlock):
        return ProseBlock(norm_inlines(elem.inlines))
    elif isinstance(elem, GrammarBlock):
        return GrammarBlock(
            elem.term,
            [norm_inlines(p) for p in elem.productions],
            elem.qualifier,
            elem.anchor_id,
        )
    elif isinstance(elem, PreBlock):
        return PreBlock(elem.text)
    elif isinstance(elem, TocHeading):
        return TocHeading(elem.section_id.strip(), elem.title.strip())
    else:
        return TocList(_norm_toc_items(elem.items))


def norm_footnote(fn: Footnote) -> Footnote:
    return Footnote(fn.id, norm_inlines(fn.inlines))


def norm_page(page: Page) -> Page:
    return Page(
        header=Header(page.header.bold_text.strip(), page.header.rest_text.strip()),
        main=[norm_main_element(e) for e in page.main],
        footnotes=[norm_footnote(f) for f in page.footnotes],
        footer=page.footer,
    )
