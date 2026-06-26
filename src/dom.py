"""
DOM classes representing a parsed page of the C standard document.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import List, Optional, Union

# ---------------------------------------------------------------------------
# Token classification (used by both PreBlock and inline CodeNode)
# ---------------------------------------------------------------------------


class TokenKind(Enum):
    """Semantic classification for a run of text inside a pre block or inline code."""

    PLAIN = "plain"
    PLACEHOLDER = "placeholder"
    KEYWORD = "kw"
    MACRO = "macro"
    COMMENT = "comment"
    COMMENT_KW = "comment-kw"
    FUNCTION = "fn"
    TYPE_GENERIC_MACRO = "tg-macro"
    CONSTANT = "const"
    IDENTIFIER = "ident"
    LITERAL = "lit"
    TYPENAME = "typename"
    GENERIC = "generic"
    ATTRIBUTE = "attr"
    STRING = "str"
    WIDE_STRING = "wide-str"
    MEMBER = "member"


# ---------------------------------------------------------------------------
# Inline nodes
# ---------------------------------------------------------------------------


@dataclass
class TextNode:
    """Plain text."""

    text: str


@dataclass
class CodeNode:
    """Inline code <code>."""

    text: str
    kind: TokenKind = TokenKind.PLAIN


@dataclass
class DfnNode:
    """Definition term <dfn>."""

    text: str


@dataclass
class VarNode:
    """Italic variable name <var> (single-character italic)."""

    text: str


@dataclass
class SupNode:
    """Superscript <sup>, optionally with an id attribute."""

    id: Optional[str]  # e.g. "footnoteref-6" or None
    children: List[Inline]


@dataclass
class LinkNode:
    """Hyperlink <a>."""

    href: str
    children: List[Inline]


@dataclass
class ItalicNode:
    """Plain italic <i> (e.g. document titles in normative references)."""

    text: str


@dataclass
class NonterminalNode:
    """Grammar nonterminal reference <i class="nonterminal">."""

    text: str


@dataclass
class MathNode:
    """Inline MathML expression <math>...</math>."""

    mathml: str


Inline = Union[
    TextNode,
    CodeNode,
    DfnNode,
    VarNode,
    SupNode,
    LinkNode,
    ItalicNode,
    NonterminalNode,
    MathNode,
]


# ---------------------------------------------------------------------------
# Block content within a ParagraphBlock
# ---------------------------------------------------------------------------


@dataclass
class ProseBlock:
    """A <p> element inside a paragraph div."""

    inlines: List[Inline] = field(default_factory=list[Inline])


@dataclass
class PreToken:
    """A run of text inside a <pre> block with optional syntax classification."""

    text: str
    kind: TokenKind = TokenKind.PLAIN


@dataclass
class PreBlock:
    """A <pre> element (code block)."""

    text: str
    tokens: Optional[List["PreToken"]] = None


@dataclass
class BridgeContent:
    """
    Raw inline content between two <pre> elements (not wrapped in <p>).
    These are lines of text that introduce the next code example.
    """

    inlines: List[Inline] = field(default_factory=list[Inline])


@dataclass
class BulletItem:
    """A <li> element inside a <ul>."""

    id: Optional[str]
    inlines: List[Inline] = field(default_factory=list[Inline])
    nested: Optional[BulletList] = None


@dataclass
class BulletList:
    """A <ul> element."""

    items: List[BulletItem] = field(default_factory=list[BulletItem])


@dataclass
class OrderedItem:
    """A <li> element inside an <ol>."""

    inlines: List[Inline] = field(default_factory=list[Inline])
    is_continuation: bool = False  # True when this item continues from the previous page


@dataclass
class OrderedList:
    """An <ol> element."""

    items: List[OrderedItem] = field(default_factory=list[OrderedItem])


@dataclass
class DefnItem:
    """A <dt>/<dd> pair in a definition list."""

    term: List[Inline] = field(default_factory=list[Inline])
    definition: List[Inline] = field(default_factory=list[Inline])


@dataclass
class DefnList:
    """A <dl> element."""

    items: List[DefnItem] = field(default_factory=list[DefnItem])


@dataclass
class GrammarBlock:
    """A grammar production definition <dl class="grammar">."""

    term: str
    productions: List[List[Inline]]
    qualifier: str = ""
    anchor_id: Optional[str] = None


ParagraphContent = Union[
    ProseBlock, PreBlock, BridgeContent, BulletList, OrderedList, DefnList, GrammarBlock
]


# ---------------------------------------------------------------------------
# Top-level page elements
# ---------------------------------------------------------------------------


@dataclass
class Heading:
    """A section heading (<h4>, <h5>, etc.)."""

    section_id: str  # e.g. "5.2.2.3"; empty string for unnumbered headings
    title: str
    explicit_level: Optional[int] = None  # overrides computed level when set
    title_inlines: Optional[List[Inline]] = None  # rich inlines when title contains code/links
    subheading: bool = False  # True for unnumbered section-label headings (Synopsis, Returns, …)

    @property
    def level(self) -> int:
        """Heading level: explicit_level if set, otherwise derived from section_id depth."""
        if self.explicit_level is not None:
            return self.explicit_level
        return len(self.section_id.split("."))


@dataclass
class ParagraphBlock:
    """A paragraph container <div class="p">."""

    id: str  # e.g. "5.2.2.3.1p1"
    num: int  # paragraph number shown in margin
    children: List[ParagraphContent] = field(default_factory=list[ParagraphContent])


@dataclass
class ForwardRefs:
    """A <div class="forward-refs"> element."""

    inlines: List[Inline] = field(default_factory=list[Inline])


@dataclass
class ISOCoverBlock:
    """ISO/IEC cover block: standard ref + document title."""

    standard_ref: str  # e.g. "ISO/IEC 9899:202y"
    title: str  # e.g. "Information technology — Programming languages — C"


@dataclass
class TocHeading:
    """Top-level TOC entry rendered as h2 with an anchor link."""

    section_id: str  # e.g. "1", "A"
    title: str  # e.g. "Scope"


@dataclass
class TocItem:
    """A single Table of Contents entry, with optional nested children."""

    section_id: str
    title: str
    children: List[TocItem] = field(default_factory=list["TocItem"])


@dataclass
class TocList:
    """A (possibly nested) Table of Contents list."""

    items: List[TocItem] = field(default_factory=list[TocItem])


MainElement = Union[
    Heading,
    ParagraphBlock,
    ForwardRefs,
    OrderedList,
    BulletList,
    DefnList,
    ISOCoverBlock,
    ProseBlock,
    TocHeading,
    TocList,
    GrammarBlock,
    PreBlock,
]


@dataclass
class SectionBlock:
    """A top-level document section wrapping a group of MainElements."""

    slug: str  # e.g. "abstract", "language-syntax-summary"
    elements: List[MainElement] = field(default_factory=list[MainElement])


# ---------------------------------------------------------------------------
# Footnote
# ---------------------------------------------------------------------------


@dataclass
class Footnote:
    """A footnote <p> element with an id like "footnote-6"."""

    id: str
    inlines: List[Inline] = field(default_factory=list[Inline])


# ---------------------------------------------------------------------------
# Header / Footer
# ---------------------------------------------------------------------------


@dataclass
class Header:
    """Page header: bold title + dash + subtitle."""

    bold_text: str  # the <b> portion
    rest_text: str  # the plain text portion after bold


@dataclass
class Footer:
    """Page footer."""

    current_section: str  # e.g. "§ 5.2.2.3.4"
    copyright: str  # e.g. "© ISO/IEC 202y — All rights reserved"
    current_clause: str  # e.g. "Environment"
    page: str  # e.g. "13", "xv", "iv"


# ---------------------------------------------------------------------------
# Page
# ---------------------------------------------------------------------------


@dataclass
class Page:
    """A single page of the document."""

    header: Header
    main: List[MainElement] = field(default_factory=list[MainElement])
    footnotes: List[Footnote] = field(default_factory=list[Footnote])
    footer: Optional[Footer] = None
