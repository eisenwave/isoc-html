"""
Parse a PDF page of the C standard document into a Page DOM.
"""

# pyright: basic
from __future__ import annotations

import re
from typing import Any, Dict, List, Optional, Sequence, Tuple

import fitz

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
    PreToken,
    ProseBlock,
    SupNode,
    TextNode,
    TocHeading,
    TocItem,
    TocList,
    TokenKind,
    VarNode,
)

# ---------------------------------------------------------------------------
# Layout constants
# ---------------------------------------------------------------------------
HEADER_Y_MAX = 65.0  # header region (top of page)
FOOTER_Y_MIN = 780.0  # footer region (bottom of page)
GUTTER_X_MAX = 78.0  # paragraph numbers live here (left of main margin)
MAIN_X_MIN = 75.0  # main content margin
BULLET_X_MIN = 85.0  # bullet items start around x=87–96
BULLET_X_MAX = 115.0  # em-dash x0 must be below this
NESTED_BULLET_X_MIN = 115.0  # nested (•) bullets start around x=123
RIGHT_MARGIN_X = 505.0  # right edge: lines past this may end with word-break hyphen
FOOTNOTE_SMALL_SIZE = 9.0  # footnote body text is ≤8pt
CODE_BLOCK_GRAY = 0.9  # fill level that indicates a code-block box

SECTION_ID_RE = re.compile(r"^(\d+|[A-Z])(\.\d+)*$")
ANNEX_HEADING_RE = re.compile(r"^Annex ([A-Z])$")
_ORDERED_ITEM_RE = re.compile(r"^\d+\.$")
_UNNUMBERED_HEADING_TEXTS = frozenset(
    {
        "Syntax",
        "Constraints",
        "Semantics",
        "Description",
        "Returns",
        "Runtime-constraints",
        "Environmental limits",
        "Recommended practice",
        "Foreword",
        "Introduction",
        "Synopsis",
        "Errors",
        "Bibliography",
        "Index",
        "Implementation limits",
    }
)
# fixed level for specific unnumbered headings; else computed from context
_UNNUMBERED_HEADING_LEVELS: dict = {"Foreword": 1, "Introduction": 1, "Bibliography": 1, "Index": 1}
# unnumbered headings that should also carry an id attribute (for TOC anchors)
_UNNUMBERED_HEADING_IDS = frozenset({"Bibliography", "Index"})


# ---------------------------------------------------------------------------
# Font helpers
# ---------------------------------------------------------------------------


def _is_mono(font: str) -> bool:
    return "Mono" in font or "Courier" in font


def _is_math(font: str) -> bool:
    """Return True for Computer Modern math fonts (CMSY, CMMI, CMEX, CMR…)."""
    return font.startswith("CM")


def _is_bold(font: str) -> bool:
    return "Bold" in font


def _is_italic(font: str) -> bool:
    return "Ital" in font or "Oblique" in font or font.endswith("Ob")


def _classify_span_kind(color: int, bold: bool, italic: bool) -> TokenKind:
    """Map a mono span's color+style to a TokenKind for syntax highlighting."""
    if color == 0x00008C and bold and italic:
        return TokenKind.GENERIC
    if italic:
        return TokenKind.PLACEHOLDER
    if color == 0x8C0000 and bold:
        return TokenKind.MACRO
    if color == 0x8C007A:
        return TokenKind.COMMENT_KW if bold else TokenKind.COMMENT
    if color == 0x005900:
        return TokenKind.FUNCTION
    if color == 0x546600:
        return TokenKind.TYPE_GENERIC_MACRO
    if color == 0x610040:
        return TokenKind.CONSTANT
    if color == 0x29295C:
        return TokenKind.IDENTIFIER
    if color == 0x0000FF and bold:
        return TokenKind.LITERAL
    if color == 0x00008C and bold:
        return TokenKind.TYPENAME
    if color == 0x612661:
        return TokenKind.ATTRIBUTE
    if color == 0x592626:
        return TokenKind.STRING
    if color == 0x590026:
        return TokenKind.WIDE_STRING
    if color == 0x000073:
        return TokenKind.MEMBER
    if color == 0x000000 and bold:
        return TokenKind.KEYWORD
    return TokenKind.PLAIN


def _is_math_font(font: str) -> bool:
    """Return True for Computer Modern and Latin Modern math fonts (CM*/LM*)."""
    return font.startswith("CM") or font.startswith("LM")


# Characters that are mathematical operators in CM symbol fonts.
# ∞ is intentionally excluded: it is a mathematical constant (mi), not an operator.
_MATH_OP_CHARS = frozenset("⌊⌋⌈⌉⟨⟩−+×÷≤≥≠≈∑∏∫∂·")


def _math_char_type(ch: str, font: str) -> str:
    """Return the MathML element name for ch within a math font span.

    Returns 'mi', 'mn', 'mo', or '' (whitespace / skip).
    """
    if ch in " \t\n":
        return ""
    if ch == "\u221e":  # ∞ — a named constant, not an operator
        return "mi"
    if ch in _MATH_OP_CHARS:
        return "mo"
    if ch.isdigit() or ch == ".":
        return "mn"
    if font.startswith("CMMI") or font.startswith("LMMI"):
        return "mi"
    if ch.isalpha():
        return "mi"
    return "mo"  # fallback for other CM chars


def _span_level(size: float) -> int:
    """Map a span's font size to a MathML nesting level.

    Level 0 = base (≥9 pt), 1 = first sup/sub (~7 pt), 2 = second (~5 pt).
    """
    if size >= 9.0:
        return 0
    elif size >= 5.5:
        return 1
    return 2


def _tokenise_math_span(span: dict) -> list[tuple[str, str, int, float]]:
    """Convert one math font span to a list of (text, font, level, y0) tokens.

    Multi-char all-alpha tokens in CMR/LMR (e.g. 'min', 'max') are kept as a
    single <mi> identifier.  Numeric tokens (digits + decimal point) are also
    kept as a single <mn> token.  All other multi-char spans are split per
    character.  Leading spaces are stripped (PDF kerning artefact).
    """
    font = span["font"]
    size = span["size"]
    y0 = float(span["bbox"][1])
    level = _span_level(size)
    text: str = span["text"]
    if text.startswith(" "):
        text = text[1:]
    if not text:
        return []
    if len(text) > 1:
        # Pure-alpha identifier (CMR/LMR 'min', 'max'; CMMI/LMMI identifiers).
        if text.isalpha() and (
            font.startswith("CMR")
            or font.startswith("LMR")
            or font.startswith("CMMI")
            or font.startswith("LMMI")
        ):
            return [(text, font, level, y0)]
        # Numeric literal (integer or decimal, e.g. "2.4", "279").
        if text[0].isdigit() and all(c.isdigit() or c == "." for c in text):
            return [(text, font, level, y0)]
        # All other multi-char spans: split character-by-character, skipping spaces.
        return [(ch, font, level, y0) for ch in text if ch != " "]
    return [(text, font, level, y0)]


def _token_to_mathml(text: str, font: str) -> str:
    """Render a (text, font) token as a MathML element string."""
    if len(text) == 1:
        t = _math_char_type(text, font)
        if not t:
            t = "mo"
        esc = text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
        return f"<{t}>{esc}</{t}>"
    # Multi-char token.
    if text.isalpha():
        elem = "mi"
    elif text[0].isdigit() and all(c.isdigit() or c == "." for c in text):
        elem = "mn"
    else:
        elem = "mo"
    esc = text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
    return f"<{elem}>{esc}</{elem}>"


def _build_mathml_group(
    tokens: list[tuple[str, str, int, float]],
    base_level: int,
) -> str:
    """Recursively build MathML for a list of tokens rooted at *base_level*.

    Tokens at base_level are treated as base atoms.  Any immediately following
    run of tokens at deeper levels is collected as a child group.  The average
    y0 of direct children (base_level+1) versus the base atom's y0 determines
    whether the child is a superscript or subscript.  Recursion handles multiple
    nesting levels (e.g. b^{e_min}).
    """
    _Y_THRESH = 0.5  # pt — minimum vertical shift to classify as sup/sub

    elements: list[str] = []
    i = 0
    while i < len(tokens):
        text, font, level, y0 = tokens[i]
        if level != base_level:
            # Orphan token at wrong level — emit as-is and keep moving.
            elements.append(_token_to_mathml(text, font))
            i += 1
            continue

        base_elem = _token_to_mathml(text, font)
        base_y0 = y0
        i += 1

        # Collect following tokens that belong to a deeper level.
        child_tokens: list[tuple[str, str, int, float]] = []
        while i < len(tokens) and tokens[i][2] > base_level:
            child_tokens.append(tokens[i])
            i += 1

        if not child_tokens:
            elements.append(base_elem)
            continue

        # Use y0 of direct (base_level+1) children to decide sup vs sub.
        direct = [t for t in child_tokens if t[2] == base_level + 1]
        if not direct:
            elements.append(base_elem)
            continue

        avg_y0 = sum(t[3] for t in direct) / len(direct)
        child_ml = _build_mathml_group(child_tokens, base_level + 1)

        if avg_y0 < base_y0 - _Y_THRESH:
            elements.append(f"<msup>{base_elem}{child_ml}</msup>")
        elif avg_y0 > base_y0 + _Y_THRESH:
            elements.append(f"<msub>{base_elem}{child_ml}</msub>")
        else:
            # No significant vertical shift — treat as same baseline.
            elements.append(base_elem)

    if not elements:
        return ""
    if len(elements) == 1:
        return elements[0]
    return "<mrow>" + "".join(elements) + "</mrow>"


def _is_mn_token(text: str, font: str) -> bool:
    """Return True if this token would render as a <mn> element."""
    if len(text) == 1:
        return _math_char_type(text, font) == "mn"
    return text[0].isdigit() and all(c.isdigit() or c == "." for c in text)


def _merge_adjacent_mn(
    tokens: list[tuple[str, str, int, float]],
) -> list[tuple[str, str, int, float]]:
    """Merge consecutive same-level mn tokens into a single numeric token.

    Restores the behaviour of the old code that merged e.g. '2', '.', '4' into
    a single <mn>2.4</mn> element.
    """
    result: list[tuple[str, str, int, float]] = []
    i = 0
    while i < len(tokens):
        text, font, level, y0 = tokens[i]
        if _is_mn_token(text, font):
            j = i + 1
            while (
                j < len(tokens)
                and tokens[j][2] == level
                and _is_mn_token(tokens[j][0], tokens[j][1])
            ):
                j += 1
            if j > i + 1:
                merged = "".join(t[0] for t in tokens[i:j])
                result.append((merged, font, level, y0))
                i = j
                continue
        result.append(tokens[i])
        i += 1
    return result


def _build_mathml(spans: list[dict]) -> str:
    """Convert a sequence of CM/LM-font spans to MathML inner content.

    Uses span y-position and size to build proper super/subscript hierarchy.
    Returns an empty string if the content is not genuinely mathematical.
    """
    tokens: list[tuple[str, str, int, float]] = []
    for span in spans:
        tokens.extend(_tokenise_math_span(span))

    if not tokens:
        return ""

    # Merge consecutive numeric atoms (e.g. '2', '.', '4' → '2.4').
    tokens = _merge_adjacent_mn(tokens)

    # Require at least one identifier or digit (not purely operators/punctuation).
    has_real_math = any(
        (len(t[0]) > 1 and t[0].isalpha())
        or _is_mn_token(t[0], t[1])
        or (len(t[0]) == 1 and _math_char_type(t[0], t[1]) == "mi")
        for t in tokens
    )
    if not has_real_math:
        return ""

    base_level = min(t[2] for t in tokens)
    return _build_mathml_group(tokens, base_level)


# Characters after which no extra space is needed following a MathNode.
_NO_SPACE_AFTER_MATH = frozenset(" .,;:!?)]}'\"-")


def _add_math_spacing(inlines: list[Inline]) -> list[Inline]:
    """Insert TextNode(' ') after a MathNode when the next text starts with a word char."""
    result: list[Inline] = []
    for i, node in enumerate(inlines):
        result.append(node)
        if isinstance(node, MathNode) and i + 1 < len(inlines):
            nxt = inlines[i + 1]
            if isinstance(nxt, TextNode) and nxt.text and nxt.text[0] not in _NO_SPACE_AFTER_MATH:
                result.append(TextNode(" "))
    return result


def _dfns_to_nonterminals(inlines: list[Inline]) -> list[Inline]:
    """Convert DfnNode/VarNode items to NonterminalNode in a grammar production.

    ``_merge_adjacent_dfns`` can combine ``group`` and ``group-part`` (both
    italic on the same line) into a single ``DfnNode('group group-part')``.
    This function splits those back into one ``NonterminalNode`` per word.
    """
    result: list[Inline] = []
    for node in inlines:
        if isinstance(node, (DfnNode, VarNode)):
            parts = node.text.split()
            for i, part in enumerate(parts):
                if i > 0:
                    result.append(TextNode(" "))
                result.append(NonterminalNode(part))
        else:
            result.append(node)
    return result


def _merge_adjacent_dfns(inlines: list[Inline]) -> list[Inline]:
    """Merge consecutive DfnNodes (or ItalicNodes) separated only by whitespace TextNodes.

    The PDF often splits a multi-word italic term (e.g. 'source files') into
    separate per-word spans.  This pass reunites them.
    VarNodes (single-character italic) are intentionally left alone.
    Also merges adjacent LinkNodes that share the same href (e.g. split URLs).
    """
    result: list[Inline] = []
    i = 0
    while i < len(inlines):
        node = inlines[i]
        if isinstance(node, DfnNode):
            merged = node.text
            j = i + 1
            while j + 1 < len(inlines):
                sep = inlines[j]
                nxt = inlines[j + 1]
                if (
                    not isinstance(sep, TextNode)
                    or sep.text.strip()
                    or not isinstance(nxt, DfnNode)
                ):
                    break
                merged += sep.text + nxt.text
                j += 2
            result.append(DfnNode(merged))
            i = j
        elif isinstance(node, ItalicNode):
            merged = node.text
            j = i + 1
            while j + 1 < len(inlines):
                sep = inlines[j]
                nxt = inlines[j + 1]
                if (
                    not isinstance(sep, TextNode)
                    or sep.text.strip()
                    or not isinstance(nxt, ItalicNode)
                ):
                    break
                merged += sep.text + nxt.text
                j += 2
            result.append(ItalicNode(merged))
            i = j
        elif isinstance(node, LinkNode):
            # Merge adjacent LinkNodes with same href (e.g. URL split across lines)
            merged_children: list[Inline] = list(node.children)
            j = i + 1
            while j < len(inlines):
                nxt = inlines[j]
                if not isinstance(nxt, LinkNode) or nxt.href != node.href:
                    break
                merged_children.extend(nxt.children)
                j += 1
            # Coalesce adjacent CodeNode children with the same kind (e.g. "https:" + "//www...")
            coalesced: list[Inline] = []
            code_acc = ""
            code_acc_kind: TokenKind = TokenKind.PLAIN
            for child in merged_children:
                if isinstance(child, CodeNode):
                    if child.kind != code_acc_kind and code_acc:
                        coalesced.append(CodeNode(code_acc, code_acc_kind))
                        code_acc = ""
                    code_acc += child.text
                    code_acc_kind = child.kind
                else:
                    if code_acc:
                        coalesced.append(CodeNode(code_acc, code_acc_kind))
                        code_acc = ""
                        code_acc_kind = TokenKind.PLAIN
                    coalesced.append(child)
            if code_acc:
                coalesced.append(CodeNode(code_acc, code_acc_kind))
            result.append(LinkNode(node.href, coalesced))
            i = j
        else:
            result.append(node)
            i += 1
    return result


def _make_text_leaf(text: str, font: str, italic_as_i: bool = False) -> list[Inline]:
    """Create leaf inline nodes for a span, splitting leading/trailing spaces out of dfn/var."""
    if _is_mono(font):
        return [CodeNode(text)]
    # Whitespace-only spans carry no semantic meaning regardless of font
    if not text.strip():
        return [TextNode(text)]
    if _is_italic(font) and not _is_bold(font):
        stripped = text.strip()
        leading = text[: len(text) - len(text.lstrip())]
        trailing = text[len(text.rstrip()) :]
        nodes: list[Inline] = []
        if leading:
            nodes.append(TextNode(leading))
        if italic_as_i:
            nodes.append(ItalicNode(stripped))
        elif len(stripped) == 1 and stripped.isalpha():
            nodes.append(VarNode(stripped))
        else:
            nodes.append(DfnNode(stripped))
        if trailing:
            nodes.append(TextNode(trailing))
        return nodes
    return [TextNode(text)]


# ---------------------------------------------------------------------------
# Link helpers
# ---------------------------------------------------------------------------


def _nameddest_to_href(nameddest: str) -> str:
    """Convert a PDF named destination to an HTML href fragment."""
    if nameddest.startswith("H"):
        # e.g. Hfootnote.6  →  #footnote-6
        rest = nameddest[1:]
        return "#" + rest.replace(".", "-")
    else:
        # e.g. chapter.0.7         → #7
        #      subsection.0.7.1.1  → #7.1.1
        parts = nameddest.split(".")
        return "#" + ".".join(parts[2:])


# ---------------------------------------------------------------------------
# Main parser class
# ---------------------------------------------------------------------------


class PageParser:
    """Parses a single fitz.Page into the Page DOM."""

    def __init__(self, page: fitz.Page):
        self.page = page
        self.data: Any = page.get_text(
            "dict",
            flags=fitz.TEXT_PRESERVE_WHITESPACE | fitz.TEXT_PRESERVE_LIGATURES,
        )
        self.blocks: List[dict] = self.data["blocks"]
        self.links: List[dict] = page.get_links()
        self.gray_rects: List[fitz.Rect] = self._find_gray_rects()
        self.footnote_sep_y: Optional[float] = self._find_footnote_sep_y()
        self.italic_as_i: bool = self._detect_italic_as_i()

    # ------------------------------------------------------------------
    # Setup helpers
    # ------------------------------------------------------------------

    def _detect_italic_as_i(self) -> bool:
        """Return True when italic text on this page should render as <i> not <dfn>.

        Currently detects the "Normative references" section heading (section 2),
        where italic is used for document titles rather than definition terms.
        """
        for block in self.blocks:
            if block["type"] != 0:
                continue
            text = "".join(s["text"] for ln in block["lines"] for s in ln["spans"]).strip()
            if text in (
                "Normative references",
                "2 Normative references",
                "2. Normative references",
            ):
                return True
        return False

    def _find_gray_rects(self) -> List[fitz.Rect]:
        """Return merged list of gray-filled rectangles (code-block boxes)."""
        rects = []
        for d in self.page.get_drawings():
            fill = d.get("fill")
            if fill and len(fill) == 3 and all(c >= CODE_BLOCK_GRAY for c in fill):
                rects.append(fitz.Rect(d["rect"]))
        return self._merge_rectangles(rects)

    @staticmethod
    def _merge_rectangles(rectangles: List[fitz.Rect]) -> List[fitz.Rect]:
        if not rectangles:
            return []
        rectangles = sorted(rectangles, key=lambda r: (r.y0, r.x0))
        merged = [fitz.Rect(rectangles[0])]
        for r in rectangles[1:]:
            prev = merged[-1]
            if r.y0 <= prev.y1 + 2 and r.x0 <= prev.x1 + 2:
                merged[-1] = fitz.Rect(
                    min(prev.x0, r.x0),
                    min(prev.y0, r.y0),
                    max(prev.x1, r.x1),
                    max(prev.y1, r.y1),
                )
            else:
                merged.append(fitz.Rect(r))
        return merged

    def _find_footnote_sep_y(self) -> Optional[float]:
        """Return y-coordinate of the black horizontal rule separating footnotes.

        The rule must be in the lower half of the page (y > 400) to avoid
        misidentifying the header border line.  We take the *lowest* such line
        because table-row borders also match the same geometric criteria and
        appear earlier (at smaller y values) on pages that contain tables.
        """
        best: Optional[float] = None
        for d in self.page.get_drawings():
            color = d.get("color")
            fill = d.get("fill")
            rect = fitz.Rect(d["rect"])
            if color == (0.0, 0.0, 0.0) and fill is None and rect.height < 2 and rect.y0 > 400:
                if best is None or rect.y0 > best:
                    best = rect.y0
        return best

    def _is_in_gray_rect(self, bbox: Sequence[float]) -> bool:
        br = fitz.Rect(bbox)
        for gr in self.gray_rects:
            if (
                br.x0 >= gr.x0 - 6
                and br.x1 <= gr.x1 + 6
                and br.y0 >= gr.y0 - 6
                and br.y1 <= gr.y1 + 6
            ):
                return True
        return False

    def _links_for_line(self, line_y0: float, line_y1: float) -> List[dict]:
        """Return links whose 'from' rect intersects the given line y-range."""
        result = []
        for link in self.links:
            lr = fitz.Rect(link["from"])
            if lr.y0 < line_y1 + 5 and lr.y1 > line_y0 - 5:
                result.append(link)
        return result

    # ------------------------------------------------------------------
    # Span-level inline parsing
    # ------------------------------------------------------------------

    def _find_link_split(
        self,
        text: str,
        href: str,
        span: dict,
        link_rect: fitz.Rect,
    ) -> Optional[Tuple[int, int]]:
        """
        Find (start, end) character indices of the linked text within span text.
        Uses expected text from href as primary lookup, falling back to x-position estimate.
        """
        if href.startswith("#footnote-"):
            fn_num = href[len("#footnote-") :]
            needle = fn_num + ")"
        elif href == "#":
            return None
        elif href.startswith("http://") or href.startswith("https://"):
            needle = href.split("://", 1)[1]
        else:
            needle = href[1:]  # strip '#'

        sx0, sx1 = span["bbox"][0], span["bbox"][2]
        span_width = sx1 - sx0
        if span_width > 0:
            est_start = int(max(0, (link_rect.x0 - sx0) / span_width * len(text) - 3))
        else:
            est_start = 0

        idx = text.find(needle, est_start)
        if idx == -1:
            idx = text.find(needle, 0)
        if idx != -1:
            return (idx, idx + len(needle))
        return None

    def _span_to_inlines(
        self, span: dict, link: Optional[dict], is_link_full: bool
    ) -> List[Inline]:
        """
        Convert a single span to inline nodes.
        If link is given and is_link_full=True, wrap the whole span as a link.
        """
        text = span["text"]
        font = span["font"]
        size = span["size"]
        is_sup = size < 8.5 and not _is_mono(font)

        def _make_leaf(t: str) -> list[Inline]:
            return _make_text_leaf(t, font, self.italic_as_i)

        if not link:
            leaves = _make_leaf(text)
            if is_sup:
                return [SupNode(None, leaves)]
            return leaves

        href = _nameddest_to_href(link["nameddest"])
        leaves = _make_leaf(text)

        if is_sup or href.startswith("#footnote-"):
            fn_num = href[len("#footnote-") :]
            sup_id = f"footnoteref-{fn_num}"
            return [SupNode(sup_id, [LinkNode(href, leaves)])]
        else:
            return [LinkNode(href, leaves)]

    def _spans_to_inlines_with_links(
        self,
        spans: List[dict],
        line_links: List[dict],
    ) -> List[Inline]:
        """
        Convert a list of spans (one PDF line) to inline nodes, handling link splits.
        Code spans are merged when adjacent; leading spaces on code spans are separated.
        """
        result: List[Inline] = []
        code_buf = ""
        code_kind: TokenKind = TokenKind.PLAIN
        prev_was_code = False
        math_buf: list[dict] = []

        def flush_code() -> None:
            nonlocal code_buf, code_kind, prev_was_code
            if code_buf:
                result.append(CodeNode(code_buf, code_kind))
                code_buf = ""
                code_kind = TokenKind.PLAIN
                prev_was_code = False

        def flush_math() -> None:
            if not math_buf:
                return
            buf = list(math_buf)
            math_buf.clear()
            # Extract leading space from the first span.
            first_text = buf[0]["text"]
            if first_text.startswith(" "):
                result.append(TextNode(" "))
                buf[0] = dict(buf[0])
                buf[0]["text"] = first_text[1:]
            mathml = _build_mathml(buf)
            if mathml:
                result.append(MathNode(mathml))
            else:
                # Not genuinely mathematical — emit as plain text.
                for sp in buf:
                    t = sp["text"]
                    if t.strip():
                        result.extend(_make_text_leaf(t, sp["font"], self.italic_as_i))
                    elif t:
                        result.append(TextNode(t))

        for span in spans:
            text = span["text"]
            if not text:
                continue
            font = span["font"]
            size = span["size"]
            span_rect = fitz.Rect(span["bbox"])
            is_sup = size < 7.5 and not _is_mono(font)

            # Find links that overlap with this span (by x-coordinate)
            overlapping = [
                lk
                for lk in line_links
                if fitz.Rect(lk["from"]).x0 < span_rect.x1 + 2
                and fitz.Rect(lk["from"]).x1 > span_rect.x0 - 2
            ]

            if _is_mono(font):
                flush_math()
                # Check if a URI link fully covers this monospace span → produce a link
                uri_link = next(
                    (
                        lk
                        for lk in overlapping
                        if "uri" in lk
                        and fitz.Rect(lk["from"]).x0 <= span_rect.x0 + 5
                        and fitz.Rect(lk["from"]).x1 >= span_rect.x1 - 5
                    ),
                    None,
                )
                if uri_link:
                    flush_code()
                    t = text
                    if t.startswith(" "):
                        result.append(TextNode(" "))
                        t = t[1:]
                    t = t.strip()
                    if t:
                        span_kind = _classify_span_kind(
                            span["color"], _is_bold(font), _is_italic(font)
                        )
                        result.append(LinkNode(uri_link["uri"], [CodeNode(t, span_kind)]))
                else:
                    # Regular code span: strip leading space and push as text if not inside code run
                    t = text
                    if t.startswith(" ") and not prev_was_code:
                        flush_code()
                        result.append(TextNode(" "))
                        t = t[1:]
                    if t:
                        span_kind = _classify_span_kind(
                            span["color"], _is_bold(font), _is_italic(font)
                        )
                        if span_kind != code_kind and code_buf:
                            flush_code()
                        code_buf += t
                        code_kind = span_kind
                        prev_was_code = True
                continue

            # Non-mono span: flush pending code first.
            flush_code()

            # Math font with no overlapping links → collect for MathML.
            if _is_math_font(font) and not overlapping:
                math_buf.append(span)
                continue

            # Non-math (or has links): flush pending math first.
            flush_math()

            if not overlapping:
                if is_sup:
                    result.append(SupNode(None, [TextNode(text)]))
                else:
                    result.extend(_make_text_leaf(text, font, self.italic_as_i))
                continue

            # Handle links that (partially) cover this span
            overlapping.sort(key=lambda lk: fitz.Rect(lk["from"]).x0)
            remaining = text

            for lk in overlapping:
                if "nameddest" in lk:
                    href = _nameddest_to_href(lk["nameddest"])
                elif "uri" in lk:
                    href = lk["uri"]
                else:
                    continue
                lr = fitz.Rect(lk["from"])

                # Find where the linked text sits within remaining text
                split = self._find_link_split(remaining, href, span, lr)
                if split is None:
                    # Link text not found in this span — skip this link
                    continue

                s, e = split
                # Adjust split indices to account for already-consumed text
                # (remaining is already the suffix of original text)
                before = remaining[:s]
                linked = remaining[s:e]
                remaining = remaining[e:]

                if before:
                    if is_sup:
                        result.append(SupNode(None, [TextNode(before)]))
                    else:
                        result.append(TextNode(before))

                leaf_text = linked
                if href.startswith("#footnote-"):
                    fn_num = href[len("#footnote-") :]
                    sup_id = f"footnoteref-{fn_num}"
                    result.append(SupNode(sup_id, [LinkNode(href, [TextNode(leaf_text)])]))
                elif is_sup:
                    result.append(SupNode(None, [LinkNode(href, [TextNode(leaf_text)])]))
                else:
                    result.append(LinkNode(href, [TextNode(leaf_text)]))

            # Leftover text after all links
            if remaining:
                if is_sup:
                    result.append(SupNode(None, [TextNode(remaining)]))
                else:
                    result.extend(_make_text_leaf(remaining, font, self.italic_as_i))

        flush_math()
        flush_code()
        return result

    # ------------------------------------------------------------------
    # Line-level processing
    # ------------------------------------------------------------------

    def _get_content_spans(self, line: dict) -> List[dict]:
        """Return spans from a line that are in the main content area (not gutter)."""
        return [s for s in line["spans"] if s["bbox"][0] >= MAIN_X_MIN - 2]

    def _process_lines_to_inlines(
        self,
        lines: List[dict],
        line_links_map: Dict[float, List[dict]],
    ) -> List[Inline]:
        """
        Convert multiple lines to a flat list of inlines.
        Lines are joined: a word-break hyphen at the right margin is removed
        and the next line joins without a space; otherwise a space is inserted.
        """
        all_inlines: List[List[Inline]] = []
        word_break_flags: List[bool] = []

        for line in lines:
            spans = self._get_content_spans(line)
            if not spans:
                continue
            line_y0 = line["bbox"][1]
            llinks = line_links_map.get(line_y0) or []

            # Check for word-break hyphen: last non-empty regular text span
            # ending with '-' at right edge.
            # Also detect URL line-splits where a monospace span ends with ':'
            # at the right margin (e.g. "https:" wrapped before "//www...").
            word_break = False
            for sp in reversed(spans):
                t = sp["text"]
                if t.strip():
                    if t.endswith("-") and sp["bbox"][2] > RIGHT_MARGIN_X:
                        word_break = True
                        # Remove trailing hyphen
                        modified = dict(sp)
                        modified["text"] = t[:-1]
                        idx = spans.index(sp)
                        spans = spans[:idx] + [modified] + spans[idx + 1 :]
                    elif (
                        _is_mono(sp["font"])
                        and t.rstrip().endswith(":")
                        and sp["bbox"][2] > RIGHT_MARGIN_X - 10
                    ):
                        word_break = True
                    break

            inlines = self._spans_to_inlines_with_links(spans, llinks)
            all_inlines.append(inlines)
            word_break_flags.append(word_break)

        result: List[Inline] = []
        for i, (inlines, _) in enumerate(zip(all_inlines, word_break_flags)):
            if i > 0 and not word_break_flags[i - 1]:
                result.append(TextNode(" "))
            result.extend(inlines)
        return _add_math_spacing(_merge_adjacent_dfns(result))

    # ------------------------------------------------------------------
    # Block classification
    # ------------------------------------------------------------------

    def _get_para_num(self, block: dict) -> Optional[int]:
        """Return the paragraph number if the block starts a paragraph, else None."""
        for line in block["lines"]:
            for span in line["spans"]:
                if (
                    span["bbox"][0] < GUTTER_X_MAX
                    and span["size"] < 9.5
                    and span["text"].strip().isdigit()
                ):
                    return int(span["text"].strip())
        return None

    def _is_unnumbered_heading_block(self, block: dict) -> bool:
        """Return True if this is a recognized bold unnumbered heading."""
        if block["type"] != 0:
            return False
        lines = block["lines"]
        if len(lines) != 1:
            return False
        spans = lines[0]["spans"]
        if not spans:
            return False
        text = "".join(s["text"] for s in spans).strip()
        if text not in _UNNUMBERED_HEADING_TEXTS:
            return False
        return all(_is_bold(s["font"]) for s in spans if s["text"].strip())

    def _is_heading_block(self, block: dict) -> bool:
        """Return True if all spans in this block are bold and start with a section number."""
        if block["type"] != 0:
            return False
        lines = block["lines"]
        if not lines or not lines[0]["spans"]:
            return False
        # Check only the first span of the first line (title may follow on the same line)
        first_span = lines[0]["spans"][0]
        first_span_text = first_span["text"].strip().rstrip(".")
        # "Annex X (normative/informative) Title" blocks mix bold and non-bold spans
        if ANNEX_HEADING_RE.match(first_span_text):
            return True
        if not SECTION_ID_RE.match(first_span_text):
            return False
        # The section-ID span must be non-mono bold (excludes code/synopsis blocks
        # whose first token happens to match SECTION_ID_RE).
        if not _is_bold(first_span["font"]) or _is_mono(first_span["font"]):
            return False
        # All subsequent non-mono/non-math spans must also be bold; mono (code) spans
        # (e.g. "<stdalign.h>") and math-font spans (e.g. "⌈x⌉" in §3.28) are allowed
        # to be non-bold.
        for line in lines:
            for span in line["spans"]:
                if (
                    not _is_bold(span["font"])
                    and not _is_mono(span["font"])
                    and not _is_math(span["font"])
                ):
                    return False
        return True

    def _parse_headings(self, block: dict, llmap: Dict[float, List[dict]]) -> List[Heading]:
        """Parse one or more headings from a block (handles multi-heading and same-line title)."""
        lines = block["lines"]
        headings: List[Heading] = []
        i = 0
        while i < len(lines):
            line = lines[i]
            if not line["spans"]:
                i += 1
                continue
            first_text = line["spans"][0]["text"].strip().rstrip(".")
            annex_m = ANNEX_HEADING_RE.match(first_text)
            if annex_m:
                # "Annex X" / "(normative)" / "Title" are on separate lines
                section_id = annex_m.group(1)
                i += 1  # consume "Annex X" line
                # Skip non-bold lines (e.g. "(normative)"); collect the first bold title line
                title_spans: List[dict] = []
                while i < len(lines):
                    next_spans = lines[i]["spans"]
                    if next_spans and _is_bold(next_spans[0]["font"]):
                        # Stop if this looks like the start of a new heading
                        if SECTION_ID_RE.match(next_spans[0]["text"].strip().rstrip(".")):
                            break
                        title_spans = next_spans
                        i += 1
                        break
                    i += 1
                title = "".join(s["text"] for s in title_spans).strip()
            elif SECTION_ID_RE.match(first_text):
                section_id = first_text
                # Title may be on the same line (remaining spans) or on the next line
                remaining = "".join(s["text"] for s in line["spans"][1:]).strip()
                if remaining:
                    title_spans = line["spans"][1:]
                    title = remaining
                    i += 1
                elif i + 1 < len(lines):
                    title_spans = lines[i + 1]["spans"]
                    title = "".join(s["text"] for s in title_spans).strip()
                    i += 2
                else:
                    title_spans = []
                    title = ""
                    i += 1
            else:
                i += 1
                continue
            # Build rich title inlines when the title contains mono (code) or math spans.
            title_inlines: Optional[List[Inline]] = None
            if title_spans and any(_is_mono(s["font"]) or _is_math(s["font"]) for s in title_spans):
                llinks = llmap.get(title_spans[0]["bbox"][1]) if title_spans else []
                title_inlines = self._spans_to_inlines_with_links(title_spans, llinks or [])
            headings.append(Heading(section_id, title, title_inlines=title_inlines))
        return headings

    def _is_nested_bullet_block(self, block: dict) -> bool:
        """Return True if the first content span is a bullet '\u2022' at the nested indent."""
        if block["type"] != 0:
            return False
        for line in block["lines"]:
            for span in line["spans"]:
                t = span["text"].strip()
                if t:
                    return (
                        t.startswith("\u2022")  # bullet •
                        and span["bbox"][0] >= NESTED_BULLET_X_MIN
                    )
        return False

    def _is_bullet_block(self, block: dict) -> bool:
        """Return True if the first content span is an em-dash in the bullet margin."""
        if block["type"] != 0:
            return False
        for line in block["lines"]:
            for span in line["spans"]:
                t = span["text"].strip()
                if t:
                    return (
                        t.startswith("\u2014")  # em-dash
                        and BULLET_X_MIN <= span["bbox"][0] <= BULLET_X_MAX
                    )
        return False

    def _is_defn_block(self, block: dict) -> bool:
        """True if this is a definition list term: em-dash in bullet margin followed by bold."""
        if not self._is_bullet_block(block):
            return False
        found_dash = False
        for line in block["lines"]:
            for span in line["spans"]:
                t = span["text"]
                if not found_dash:
                    stripped = t.strip()
                    if not stripped:
                        continue
                    if stripped.startswith("\u2014"):
                        found_dash = True
                        rest = t[t.index("\u2014") + 1 :].strip()
                        if rest:
                            return _is_bold(span["font"]) and not _is_mono(span["font"])
                        continue
                    else:
                        return False
                else:
                    # Definition terms are bold *prose* — bold code font is not a term.
                    return _is_bold(span["font"]) and not _is_mono(span["font"])
        return False

    def _is_forward_refs_block(self, block: dict) -> bool:
        if block["type"] != 0:
            return False
        for line in block["lines"]:
            for span in line["spans"]:
                if "Forward references" in span["text"] and _is_bold(span["font"]):
                    return True
        return False

    def _lines_start_with_bullet(self, lines: List[dict]) -> bool:
        """Return True if the first non-empty content span in *lines* is an em-dash
        positioned at the standard bullet margin.
        """
        for line in lines:
            for span in line["spans"]:
                t = span["text"].strip()
                if t:
                    return (
                        t.startswith("\u2014") and BULLET_X_MIN <= span["bbox"][0] <= BULLET_X_MAX
                    )
        return False

    # ------------------------------------------------------------------
    # Block content extraction
    # ------------------------------------------------------------------

    def _build_line_links_map(self) -> Dict[float, List[dict]]:
        """Build a mapping from line y0 → applicable links."""
        result: Dict[float, List[dict]] = {}
        for block in self.blocks:
            if block["type"] != 0:
                continue
            for line in block["lines"]:
                y0 = line["bbox"][1]
                y1 = line["bbox"][3]
                links = self._links_for_line(y0, y1)
                if links:
                    result[y0] = links
        return result

    def _block_lines_without_para_num(self, block: dict) -> List[dict]:
        """Return lines of a block, filtering out any line that is only a gutter span."""
        result = []
        for line in block["lines"]:
            content = [s for s in line["spans"] if s["bbox"][0] >= MAIN_X_MIN - 2]
            if content:
                result.append({**line, "spans": content})
        return result

    def _all_mono_lines(self, lines: list[dict]) -> bool:
        """Return True if every content span in *lines* uses a monospace font.

        Also requires that the first span is indented beyond the normal prose
        margin (x0 > 100 pt) to distinguish synopsis code blocks from rare
        single-code-word prose paragraphs.
        """
        first_x0: Optional[float] = None
        for line in lines:
            for span in line["spans"]:
                if first_x0 is None:
                    first_x0 = span["bbox"][0]
                # Non-mono italic spans are syntactic placeholder tokens (e.g.
                # "on-off-switch" in URWPalladioL-Ital) and are valid inside
                # synopsis code blocks.  Reject any other non-mono span.
                if not _is_mono(span["font"]) and not _is_italic(span["font"]):
                    return False
        if first_x0 is None:
            return False
        if first_x0 > 100.0:
            return True
        # Content starting slightly left of that threshold may still be a synopsis
        # code block if the block falls within a gray box (e.g. when the document
        # editor used a different indentation for the synopsis box).
        return first_x0 >= MAIN_X_MIN and self._is_in_gray_rect(lines[0]["bbox"])

    def _grammar_term_span(self, spans: list[dict]) -> dict | None:
        """Return the italic span that carries the nonterminal name (ends with ':').

        Handles both the normal format (italic span is first) and the Annex A
        format where a ``(section-ref)`` prefix precedes the nonterminal::

            italic '('  +  roman '6.4.1)'  +  italic ' token:'
        """
        if not spans:
            return None
        # Annex A prefix: italic '(' + roman ref + optional whitespace + italic ' term:'
        # e.g. ('(', Ital), ('6.4.1)', Roma), (' ', Ital), ('token:', Ital)
        if (
            len(spans) >= 3
            and _is_italic(spans[0]["font"])
            and spans[0]["text"].strip() == "("
            and not _is_italic(spans[1]["font"])
        ):
            # Skip past any whitespace-only italic spans following the section ref
            cand_idx = 2
            while (
                cand_idx < len(spans)
                and _is_italic(spans[cand_idx]["font"])
                and not spans[cand_idx]["text"].strip()
            ):
                cand_idx += 1
        else:
            cand_idx = 0
        if cand_idx >= len(spans):
            return None
        candidate = spans[cand_idx]
        if not _is_italic(candidate["font"]):
            return None
        if candidate["text"].strip().endswith(":"):
            return candidate
        # The colon may be in a separate roman span immediately after the term.
        next_idx = cand_idx + 1
        if next_idx < len(spans) and spans[next_idx]["text"].strip().startswith(":"):
            return candidate
        return None

    def _is_grammar_lines(self, lines: list[dict]) -> bool:
        """Return True if *lines* look like a grammar production block.

        A grammar block has:
        - Line 0: first span (or the span after an Annex A section-ref prefix) is
          italic and its text ends with ':' (the term definition).
          Non-italic text may follow on the same line (e.g. "one of" qualifier).
        - Line 1+: production alternatives, indented more than the term line
        """
        if len(lines) < 2:
            return False
        first_line = lines[0]
        spans = first_line.get("spans", [])
        if self._grammar_term_span(spans) is None:
            return False
        term_x0 = first_line["bbox"][0]
        for prod_line in lines[1:]:
            if prod_line["bbox"][0] <= term_x0 + 10:
                # A line at the term column is acceptable only if it is itself a
                # new grammar term (multi-definition block like Annex A).
                if self._grammar_term_span(prod_line.get("spans", [])) is None:
                    return False
        return True

    def _is_grammar_intro_line(self, lines: list[dict]) -> bool:
        """Return True if *lines* is a single-line Annex A grammar term with no inline
        productions (e.g. ``(6.4.2) keyword: one of``).

        These appear in the Annex A summary when the productions follow in separate
        multi-column blocks rather than in the same PDF block.
        """
        if len(lines) != 1:
            return False
        spans = lines[0].get("spans", [])
        return self._grammar_term_span(spans) is not None

    def _is_grammar_continuation_lines(self, lines: list[dict]) -> bool:
        """Return True if *lines* are production-only (no term) continuation lines.

        These appear at the start of a page when a grammar block spills across a
        page boundary.  All lines are at the production column (x0 > 130 pt) and
        contain italic or monospace-bold content typical of grammar productions.
        """
        if not lines:
            return False
        for line in lines:
            if line["bbox"][0] < 125.0:
                return False
            has_grammar_content = any(
                _is_italic(s["font"]) or (_is_mono(s["font"]) and s["text"].strip())
                for s in line.get("spans", [])
            )
            if not has_grammar_content:
                return False
        return True

    def _extract_grammar_block(
        self, lines: list[dict], llmap: Dict[float, List[dict]]
    ) -> GrammarBlock:
        """Extract a GrammarBlock from grammar production lines."""
        term_spans = lines[0].get("spans", [])
        # Skip Annex A section-ref prefix: italic '(' + roman 'ref)'
        start_idx = 0
        if (
            len(term_spans) >= 3
            and _is_italic(term_spans[0]["font"])
            and term_spans[0]["text"].strip() == "("
            and not _is_italic(term_spans[1]["font"])
        ):
            start_idx = 2
        # Term: italic text before the colon; qualifier: non-italic text after
        italic_parts: list[str] = []
        qualifier_parts: list[str] = []
        in_qualifier = False
        for span in term_spans[start_idx:]:
            if not in_qualifier and _is_italic(span["font"]):
                italic_parts.append(span["text"])
            else:
                in_qualifier = True
                qualifier_parts.append(span["text"])
        term_text = "".join(italic_parts).strip().rstrip(":")
        qualifier_text = "".join(qualifier_parts).strip().lstrip(":").strip().lstrip(":").strip()

        # Group production lines by approximate y position (handles "one of" layout
        # where all alternatives appear on the same horizontal row).
        _Y_ROW_THRESHOLD = 4.0
        y_groups: list[list[dict]] = []
        for prod_line in lines[1:]:
            y = prod_line["bbox"][1]
            if y_groups and abs(y - y_groups[-1][0]["bbox"][1]) < _Y_ROW_THRESHOLD:
                y_groups[-1].append(prod_line)
            else:
                y_groups.append([prod_line])

        productions: list[list[Inline]] = []
        for group in y_groups:
            if len(group) == 1:
                inlines = _dfns_to_nonterminals(self._process_lines_to_inlines(group, llmap))
            else:
                # Horizontal "one of" row: each item is a separate PDF line at the
                # same y.  Process them individually and join with spaces.
                sorted_group = sorted(group, key=lambda ln: ln["bbox"][0])
                inlines = []
                for j, ln in enumerate(sorted_group):
                    if j > 0:
                        inlines.append(TextNode(" "))
                    inlines.extend(
                        _dfns_to_nonterminals(self._process_lines_to_inlines([ln], llmap))
                    )
            productions.append(inlines)
        return GrammarBlock(term_text, productions, qualifier=qualifier_text)

    def _extract_grammar_blocks(
        self, lines: list[dict], llmap: Dict[float, List[dict]]
    ) -> list[GrammarBlock]:
        """Extract one or more GrammarBlocks from *lines*.

        Most blocks contain a single grammar definition, but Annex A occasionally
        merges two definitions into one PDF block.  This method splits at any
        secondary term line (at the same left x as line 0) and calls
        ``_extract_grammar_block`` on each sub-block.
        """
        if not lines:
            return []
        term_x0 = lines[0]["bbox"][0]
        # Find indices of every term line (term at the margin x0, or first line)
        split_points = [0]
        for i in range(1, len(lines)):
            ln = lines[i]
            if (
                ln["bbox"][0] <= term_x0 + 10
                and self._grammar_term_span(ln.get("spans", [])) is not None
            ):
                split_points.append(i)
        if len(split_points) == 1:
            return [self._extract_grammar_block(lines, llmap)]
        # Multiple grammar defs — split and extract each
        result: list[GrammarBlock] = []
        for k, start in enumerate(split_points):
            end = split_points[k + 1] if k + 1 < len(split_points) else len(lines)
            result.append(self._extract_grammar_block(lines[start:end], llmap))
        return result

    def _extract_grammar_block_productions(
        self, lines: list[dict], llmap: Dict[float, List[dict]]
    ) -> list[list[Inline]]:
        """Extract only the production rows from continuation grammar lines (no term line)."""
        _Y_ROW_THRESHOLD = 4.0
        y_groups: list[list[dict]] = []
        for line in lines:
            y = line["bbox"][1]
            if y_groups and abs(y - y_groups[-1][0]["bbox"][1]) < _Y_ROW_THRESHOLD:
                y_groups[-1].append(line)
            else:
                y_groups.append([line])
        productions: list[list[Inline]] = []
        for group in y_groups:
            if len(group) == 1:
                inlines = _dfns_to_nonterminals(self._process_lines_to_inlines(group, llmap))
            else:
                sorted_group = sorted(group, key=lambda ln: ln["bbox"][0])
                inlines = []
                for j, ln in enumerate(sorted_group):
                    if j > 0:
                        inlines.append(TextNode(" "))
                    inlines.extend(
                        _dfns_to_nonterminals(self._process_lines_to_inlines([ln], llmap))
                    )
            productions.append(inlines)
        return productions

    def _extract_synopsis_pre(self, lines: list[dict]) -> PreBlock:
        """Extract a synopsis code block as a PreBlock, with syntax highlighting.

        Spans in BeraSansMono oblique/italic fonts (e.g. ``BeraSansMono-BoldOb``)
        represent type-name placeholders (PLACEHOLDER).  All other mono spans are
        classified by color via :func:`_classify_span_kind` for full highlighting.
        """
        tokens: list[PreToken] = []
        first_line = True
        for line in lines:
            if not first_line:
                tokens.append(PreToken("\n"))
            first_line = False
            for span in line["spans"]:
                text = span["text"]
                if not text:
                    continue
                kind = _classify_span_kind(
                    span["color"], _is_bold(span["font"]), _is_italic(span["font"])
                )
                tokens.append(PreToken(text, kind))
        plain = "".join(t.text for t in tokens)
        has_tokens = any(t.kind != TokenKind.PLAIN for t in tokens)
        if not has_tokens:
            return PreBlock(plain, None)
        # Merge adjacent tokens of the same kind, but keep pure-whitespace tokens
        # separate from non-whitespace so that declaration anchors can find clean names.
        merged: list[PreToken] = []
        for tok in tokens:
            if merged and merged[-1].kind == tok.kind:
                prev_ws = not merged[-1].text.strip()
                tok_ws = not tok.text.strip()
                if prev_ws == tok_ws:
                    merged[-1] = PreToken(merged[-1].text + tok.text, tok.kind)
                else:
                    merged.append(tok)
            else:
                merged.append(tok)
        return PreBlock(plain, merged)

    def _extract_pre_multi(self, blocks: list[dict]) -> PreBlock:
        """Paint all code-block spans onto an ASCII canvas, building PreTokens.

        Each span is classified by font and color into a ``TokenKind`` for
        syntax highlighting.  Adjacent tokens of the same kind are merged so
        that the token list stays compact.

        Row assignment, column assignment, and whitespace-gap rules mirror the
        old plain-text ``_extract_pre_text_multi`` algorithm exactly.
        """
        _ROW_GAP_THRESHOLD = 4.0  # pt — same-row tolerance

        # Collect every span as (y0, x0, text, color, bold, italic).
        raw: list[tuple[float, float, str, int, bool, bool]] = []
        char_width = 5.0
        for block in blocks:
            for ln in block["lines"]:
                for s in ln["spans"]:
                    if not s["text"]:
                        continue
                    raw.append(
                        (
                            s["bbox"][1],
                            s["bbox"][0],
                            s["text"],
                            s["color"],
                            _is_bold(s["font"]),
                            _is_italic(s["font"]),
                        )
                    )
                    if char_width == 5.0 and s["size"] > 0:
                        char_width = s["size"] * 0.6

        if not raw:
            return PreBlock("")

        min_x0 = min((x for _, x, t, *_ in raw if t.strip()), default=raw[0][1])

        # Sort by y0 then x0; group into rows.
        raw.sort(key=lambda t: (t[0], t[1]))
        rows: list[list[tuple[float, str, int, bool, bool]]] = []
        row_y: list[float] = []
        for y0, x0, text, color, bold, italic in raw:
            if rows and abs(y0 - row_y[-1]) < _ROW_GAP_THRESHOLD:
                rows[-1].append((x0, text, color, bold, italic))
            else:
                rows.append([(x0, text, color, bold, italic)])
                row_y.append(y0)

        # Build tokens row by row, then merge adjacent same-kind tokens.
        tokens: list[PreToken] = []
        all_lines: list[str] = []

        for ri, row in enumerate(rows):
            if ri > 0:
                tokens.append(PreToken("\n"))
            canvas_len = 0
            row_chars = ""
            for x0, text, color, bold, italic in sorted(row, key=lambda t: t[0]):
                target_col = max(0, round((x0 - min_x0) / char_width))
                if canvas_len < target_col:
                    gap = " " * (target_col - canvas_len)
                    tokens.append(PreToken(gap))
                    row_chars += gap
                    canvas_len = target_col
                elif canvas_len > target_col and not row_chars.endswith(" "):
                    tokens.append(PreToken(" "))
                    row_chars += " "
                    canvas_len += 1
                kind = _classify_span_kind(color, bold, italic)
                tokens.append(PreToken(text, kind))
                row_chars += text
                canvas_len += len(text)
            all_lines.append(row_chars)

        # Merge adjacent tokens of the same kind for a compact token list.
        merged: list[PreToken] = []
        for tok in tokens:
            if merged and merged[-1].kind == tok.kind:
                merged[-1] = PreToken(merged[-1].text + tok.text, tok.kind)
            else:
                merged.append(tok)

        plain = "\n".join(all_lines)
        return PreBlock(plain, merged)

    def _is_ordered_item_block(self, block: dict) -> bool:
        """Return True if the block is an ordered-list item (starts with 'N.' marker)."""
        if block["type"] != 0:
            return False
        lines = block["lines"]
        if not lines or not lines[0]["spans"]:
            return False
        first_span = lines[0]["spans"][0]
        t = first_span["text"].strip()
        return (
            bool(_ORDERED_ITEM_RE.match(t)) and GUTTER_X_MAX < first_span["bbox"][0] < BULLET_X_MAX
        )

    def _extract_ordered_item_inlines(
        self, block: dict, llmap: Dict[float, List[dict]]
    ) -> List[Inline]:
        """Extract inline content from an ordered list item (strip the leading 'N.' marker)."""
        lines = []
        for li, line in enumerate(block["lines"]):
            if li == 0:
                spans = []
                skipped = False
                for span in line["spans"]:
                    if not skipped and _ORDERED_ITEM_RE.match(span["text"].strip()):
                        skipped = True
                        continue
                    if span["bbox"][0] >= MAIN_X_MIN - 2:
                        spans.append(span)
            else:
                spans = [s for s in line["spans"] if s["bbox"][0] >= MAIN_X_MIN - 2]
            if spans:
                lines.append({**line, "spans": spans})
        return self._process_lines_to_inlines(lines, llmap)

    def _extract_bullet_inlines(self, block: dict, llmap: Dict[float, List[dict]]) -> List[Inline]:
        """Extract inline content from a bullet item block (strip the leading '—')."""
        lines = []
        for line in block["lines"]:
            spans = []
            first_span_done = False
            for span in line["spans"]:
                t = span["text"]
                if not first_span_done and t.strip() == "\u2014":
                    first_span_done = True
                    continue
                if not first_span_done and t.startswith("\u2014"):
                    first_span_done = True
                    # Keep the rest after the dash
                    modified = dict(span)
                    modified["text"] = t[1:]
                    if modified["text"].strip():
                        spans.append(modified)
                    continue
                first_span_done = True
                if span["bbox"][0] >= MAIN_X_MIN - 2:
                    spans.append(span)
            if spans:
                lines.append({**line, "spans": spans})
        return self._process_lines_to_inlines(lines, llmap)

    def _extract_nested_bullet_inlines(
        self, block: dict, llmap: Dict[float, List[dict]]
    ) -> List[Inline]:
        """Extract inline content from a nested bullet block (strip leading '\u2022' and space)."""
        lines = []
        for line in block["lines"]:
            spans = []
            first_span_done = False
            for span in line["spans"]:
                t = span["text"]
                if not first_span_done:
                    stripped = t.strip()
                    if not stripped:
                        continue  # skip pure-whitespace spans before the bullet
                    first_span_done = True
                    if stripped == "\u2022" or stripped == "\u2022 ":
                        continue  # bullet marker span — skip entirely
                    if t.startswith("\u2022"):
                        # Bullet and content in same span (unusual)
                        modified = dict(span)
                        modified["text"] = t[t.index("\u2022") + 1 :].lstrip()
                        if modified["text"].strip():
                            spans.append(modified)
                        continue
                    # First real content span: keep as-is
                    if span["bbox"][0] >= MAIN_X_MIN - 2:
                        spans.append(span)
                else:
                    if span["bbox"][0] >= MAIN_X_MIN - 2:
                        spans.append(span)
            if spans:
                lines.append({**line, "spans": spans})
        return self._process_lines_to_inlines(lines, llmap)

    def _extract_defn_dt_inlines(self, block: dict, llmap: Dict[float, List[dict]]) -> List[Inline]:
        """Extract the term inlines from the first line of a defn block (after the em-dash)."""
        first_line = block["lines"][0]
        spans: List[dict] = []
        found_dash = False
        for span in first_line["spans"]:
            t = span["text"]
            if not found_dash:
                stripped = t.strip()
                if not stripped:
                    continue
                if stripped.startswith("\u2014"):
                    found_dash = True
                    rest = t[t.index("\u2014") + 1 :]
                    if rest.strip():
                        spans.append({**span, "text": rest})
                    continue
            else:
                if span["bbox"][0] >= MAIN_X_MIN - 2:
                    spans.append(span)
        if not spans:
            return []
        return self._process_lines_to_inlines([{**first_line, "spans": spans}], llmap)

    def _extract_defn_dd_from_block_tail(
        self, block: dict, llmap: Dict[float, List[dict]]
    ) -> Optional[List[Inline]]:
        """Return definition inlines from lines 2+ of a defn block, or None if only 1 line."""
        tail_lines = block["lines"][1:]
        if not tail_lines:
            return None
        lines = []
        for line in tail_lines:
            spans = [s for s in line["spans"] if s["bbox"][0] >= MAIN_X_MIN - 2]
            if spans:
                lines.append({**line, "spans": spans})
        if not lines:
            return None
        return self._process_lines_to_inlines(lines, llmap)

    def _extract_forward_refs_inlines(
        self,
        block: dict,
        llmap: Dict[float, List[dict]],
    ) -> List[Inline]:
        """Extract inline content of a Forward-references block, stripping the bold label."""
        lines = []
        for line in block["lines"]:
            spans = []
            for span in line["spans"]:
                # Skip the "Forward references:" bold label
                if "Forward references" in span["text"] and _is_bold(span["font"]):
                    continue
                spans.append(span)
            if spans:
                lines.append({**line, "spans": spans})
        return self._process_lines_to_inlines(lines, llmap)

    # ------------------------------------------------------------------
    # Abstract / TOC helpers
    # ------------------------------------------------------------------

    def _is_abstract_change_entry(self, block: dict) -> bool:
        """Like _is_bullet_block but with wider x range for abstract change entries."""
        if block["type"] != 0:
            return False
        for line in block["lines"]:
            for span in line["spans"]:
                t = span["text"].strip()
                if t:
                    return t.startswith("\u2014") and span["bbox"][0] < 130.0
        return False

    def _is_toc_top_level_block(self, block: dict) -> bool:
        """Bold block at x≈86.4 — a top-level TOC entry or 'Contents' heading."""
        if block["type"] != 0:
            return False
        if not block["lines"] or not block["lines"][0]["spans"]:
            return False
        first_span = block["lines"][0]["spans"][0]
        return _is_bold(first_span["font"]) and first_span["bbox"][0] < 90.0

    def _is_toc_item_block(self, block: dict) -> bool:
        """Non-bold block whose first span is a dotted section ID — a TOC sub-entry."""
        if block["type"] != 0:
            return False
        if not block["lines"] or not block["lines"][0]["spans"]:
            return False
        first_span = block["lines"][0]["spans"][0]
        return (
            not _is_bold(first_span["font"])
            and first_span["bbox"][0] > 90.0
            and bool(SECTION_ID_RE.match(first_span["text"].strip()))
        )

    def _extract_top_level_toc_entry(self, block: dict) -> Tuple[str, str]:
        """Extract (section_id, title) from a bold top-level TOC block."""
        # Collect spans from all lines (section id, title, and page number are
        # on separate lines in the PDF block structure)
        all_spans = [s for line in block["lines"] for s in line["spans"]]
        # Filter out dot fill-characters and the right-edge page number
        non_dot = [
            s
            for s in all_spans
            if s["text"].strip() and s["text"].strip() != "." and s["bbox"][0] < 505.0
        ]
        # Skip trailing page number (last token if it's all-digits and far right)
        if non_dot and non_dot[-1]["text"].strip().isdigit() and non_dot[-1]["bbox"][0] > 490.0:
            non_dot = non_dot[:-1]
        if not non_dot:
            return "", ""

        full_text = " ".join(s["text"].strip() for s in non_dot)

        # Annex pattern: "Annex X (informative/normative) Title..."
        m = re.match(r"Annex\s+([A-Z])\s*(?:\([^)]+\))?\s*(.*?)$", full_text)
        if m:
            return m.group(1), m.group(2).strip()

        # Numeric section: first token is a section ID
        first = non_dot[0]["text"].strip()
        if SECTION_ID_RE.match(first):
            title = " ".join(s["text"].strip() for s in non_dot[1:]).strip()
            return first, title

        # "Index", etc.
        return full_text, ""

    def _extract_toc_item_entry(self, block: dict) -> Tuple[str, str]:
        """Extract (section_id, title) from a non-bold TOC sub-entry block."""
        # Collect spans from all lines
        all_spans = [s for line in block["lines"] for s in line["spans"]]
        non_dot = [
            s
            for s in all_spans
            if s["text"].strip() and s["text"].strip() != "." and s["bbox"][0] < 505.0
        ]
        if non_dot and non_dot[-1]["text"].strip().isdigit() and non_dot[-1]["bbox"][0] > 490.0:
            non_dot = non_dot[:-1]
        if not non_dot:
            return "", ""
        section_id = non_dot[0]["text"].strip()
        title = " ".join(s["text"].strip() for s in non_dot[1:]).strip()
        return section_id, title

    def _build_toc_tree(self, items: List[Tuple[str, str]]) -> List[TocItem]:
        """Build a nested TocItem tree from a flat [(section_id, title), ...] list."""
        if not items:
            return []

        def depth(sid: str) -> int:
            return len(sid.split("."))

        root: List[TocItem] = []
        stack: List[Tuple[int, List[TocItem]]] = [(0, root)]

        for section_id, title in items:
            d = depth(section_id)
            while len(stack) > 1 and stack[-1][0] >= d:
                stack.pop()
            item = TocItem(section_id, title)
            stack[-1][1].append(item)
            stack.append((d, item.children))

        return root

    # ------------------------------------------------------------------
    # Page parse
    # ------------------------------------------------------------------

    def _detect_page_type(self, footer_blocks: List[dict]) -> str:
        """Return 'abstract', 'toc', or 'content' based on footer text."""
        for block in footer_blocks:
            for line in block["lines"]:
                for span in line["spans"]:
                    t = span["text"]
                    if "Abstract" in t:
                        return "abstract"
                    if "Contents" in t:
                        return "toc"
        return "content"

    def parse(self) -> Page:
        llmap = self._build_line_links_map()

        # Separate blocks by region
        header_blocks = []
        content_blocks = []
        footnote_blocks = []
        footer_blocks = []

        fn_sep_y = self.footnote_sep_y or FOOTER_Y_MIN

        for block in self.blocks:
            if block["type"] != 0:
                continue
            y0 = block["bbox"][1]
            if y0 < HEADER_Y_MAX:
                header_blocks.append(block)
            elif y0 >= FOOTER_Y_MIN:
                footer_blocks.append(block)
            elif fn_sep_y and y0 > fn_sep_y:
                footnote_blocks.append(block)
            else:
                content_blocks.append(block)

        header = self._parse_header(header_blocks)
        page_type = self._detect_page_type(footer_blocks)
        if page_type == "abstract":
            main_elements = self._parse_abstract(content_blocks, llmap)
        elif page_type == "toc":
            main_elements = self._parse_toc(content_blocks, llmap)
        else:
            main_elements = self._parse_content(content_blocks, llmap)
        footnotes = self._parse_footnotes(footnote_blocks, llmap)
        footer = self._parse_footer(footer_blocks)

        return Page(header, main_elements, footnotes, footer)

    # ------------------------------------------------------------------
    # Region-specific parsers
    # ------------------------------------------------------------------

    def _parse_header(self, blocks: List[dict]) -> Header:
        if not blocks:
            return Header("", "")
        block = blocks[0]
        bold_text = ""
        rest_text = ""
        for line in block["lines"]:
            for span in line["spans"]:
                if _is_bold(span["font"]):
                    bold_text += span["text"]
                else:
                    rest_text += span["text"]
        return Header(bold_text.strip(), rest_text)

    def _parse_abstract(
        self,
        blocks: List[dict],
        llmap: Dict[float, List[dict]],
    ) -> List[MainElement]:
        """Parse abstract pages (ISO cover, prose, change history bullets)."""
        elements: List[MainElement] = []
        bullet_buffer: List[dict] = []
        i = 0

        def flush_bullets() -> None:
            if bullet_buffer:
                items = [
                    BulletItem(None, self._extract_bullet_inlines(b, llmap)) for b in bullet_buffer
                ]
                elements.append(BulletList(items))
                bullet_buffer.clear()

        while i < len(blocks):
            block = blocks[i]
            text = "".join(s["text"] for ln in block["lines"] for s in ln["spans"]).strip()

            # ISO cover: block containing "INTERNATIONAL STANDARD"
            if "INTERNATIONAL STANDARD" in text:
                flush_bullets()
                standard_ref = ""
                for ln in block["lines"]:
                    for s in ln["spans"]:
                        if "ISO/IEC 9" in s["text"]:
                            standard_ref = s["text"].strip()
                # Following block is the title
                title = ""
                if i + 1 < len(blocks):
                    title = "".join(
                        s["text"] for ln in blocks[i + 1]["lines"] for s in ln["spans"]
                    ).strip()
                    i += 2
                else:
                    i += 1
                elements.append(ISOCoverBlock(standard_ref, title))
                continue

            # "Abstract" bold single-line → h1
            spans0 = block["lines"][0]["spans"] if block["lines"] else []
            is_bold_block = spans0 and all(_is_bold(s["font"]) for s in spans0 if s["text"].strip())
            is_single_line = len(block["lines"]) == 1

            if text == "Abstract" and is_bold_block:
                flush_bullets()
                elements.append(Heading("", "Abstract", explicit_level=1))
                i += 1
                continue

            # Single-line bold → section heading (e.g. "2024 January")
            if is_single_line and is_bold_block and text:
                flush_bullets()
                elements.append(Heading("", text, explicit_level=2))
                i += 1
                continue

            # Change entry (em-dash at x ≤ 130)
            if self._is_abstract_change_entry(block):
                bullet_buffer.append(block)
                i += 1
                continue

            # Regular prose
            flush_bullets()
            lines = self._block_lines_without_para_num(block)
            if lines:
                inlines = self._process_lines_to_inlines(lines, llmap)
                elements.append(ProseBlock(inlines))
            i += 1

        flush_bullets()
        return elements

    def _parse_toc(
        self,
        blocks: List[dict],
        llmap: Dict[float, List[dict]],
    ) -> List[MainElement]:
        """Parse Table of Contents pages."""
        elements: List[MainElement] = []
        pending_items: List[Tuple[str, str]] = []

        def flush_items() -> None:
            if pending_items:
                tree = self._build_toc_tree(list(pending_items))
                elements.append(TocList(tree))
                pending_items.clear()

        for block in blocks:
            if not block["lines"] or not block["lines"][0]["spans"]:
                continue
            text = "".join(s["text"] for ln in block["lines"] for s in ln["spans"]).strip()

            # "Contents" heading (h1, first block on page 5 only)
            if self._is_toc_top_level_block(block) and text == "Contents":
                flush_items()
                elements.append(Heading("", "Contents", explicit_level=1))
                continue

            # Top-level bold entry → TocHeading
            if self._is_toc_top_level_block(block):
                flush_items()
                section_id, title = self._extract_top_level_toc_entry(block)
                if section_id:
                    elements.append(TocHeading(section_id, title))
                continue

            # Sub-level entry → collect for TocList
            if self._is_toc_item_block(block):
                section_id, title = self._extract_toc_item_entry(block)
                if section_id:
                    pending_items.append((section_id, title))
                continue

        flush_items()
        return elements

    def _parse_footer(self, blocks: List[dict]) -> Optional[Footer]:
        if not blocks:
            return None
        current_section = ""
        copyright_text = ""
        current_clause = ""
        page_num = ""

        for block in blocks:
            for line in block["lines"]:
                for span in line["spans"]:
                    t = span["text"].strip()
                    # Left side: § ...
                    if t.startswith("§"):
                        current_section = t
                    # Center: © ...
                    elif t.startswith("©"):
                        copyright_text = t
                    # Right side: "ClauseName — N"
                    elif " — " in t:
                        parts = t.rsplit(" — ", 1)
                        current_clause = parts[0].strip()
                        page_num = parts[1].strip()

        return Footer(current_section, copyright_text, current_clause, page_num)

    def _parse_content(
        self,
        blocks: List[dict],
        llmap: Dict[float, List[dict]],
    ) -> List[MainElement]:
        elements: List[MainElement] = []

        # We need to group blocks into paragraphs
        # State: current heading section_id, current paragraph
        current_section_id = ""
        current_para: Optional[_ParaBuilder] = None

        def flush_para() -> None:
            nonlocal current_para
            if current_para:
                elements.append(current_para.build())
                current_para = None

        bullet_buffer: List[Tuple[dict, dict]] = []  # (block, llmap)

        def flush_bullets() -> None:
            nonlocal bullet_buffer, current_para
            if bullet_buffer:
                items: list[BulletItem] = []
                pending_nested: list[BulletItem] = []
                top_level_count = 0
                for bk, _ in bullet_buffer:
                    if self._is_nested_bullet_block(bk):
                        inlines = self._extract_nested_bullet_inlines(bk, llmap)
                        pending_nested.append(BulletItem(None, inlines))
                    else:
                        if pending_nested and items:
                            items[-1].nested = BulletList(pending_nested)
                            pending_nested = []
                        top_level_count += 1
                        item_id = (
                            f"{current_para.para_id}.{top_level_count}" if current_para else None
                        )
                        inlines = self._extract_bullet_inlines(bk, llmap)
                        items.append(BulletItem(item_id, inlines))
                if pending_nested and items:
                    items[-1].nested = BulletList(pending_nested)
                if current_para:
                    current_para.add(BulletList(items))
                bullet_buffer = []

        ordered_buffer: List[dict] = []
        # Inlines for a phantom OL item that spilled over from the previous page
        orphan_ol_continuation: Optional[List[Inline]] = None

        def flush_ordered() -> None:
            nonlocal ordered_buffer, orphan_ol_continuation
            if ordered_buffer:
                items: List[OrderedItem] = []
                if orphan_ol_continuation is not None:
                    items.append(OrderedItem(orphan_ol_continuation, is_continuation=True))
                    orphan_ol_continuation = None
                for bk in ordered_buffer:
                    inlines = self._extract_ordered_item_inlines(bk, llmap)
                    items.append(OrderedItem(inlines))
                ol = OrderedList(items)
                if current_para is not None:
                    current_para.add(ol)
                else:
                    elements.append(ol)
                ordered_buffer = []
            else:
                # No real OL items: discard any stale orphan
                orphan_ol_continuation = None

        defn_buffer: List[DefnItem] = []
        expecting_defn_dd: bool = False

        def flush_defns() -> None:
            nonlocal defn_buffer, expecting_defn_dd
            if defn_buffer:
                dl = DefnList(list(defn_buffer))
                if current_para is not None:
                    current_para.add(dl)
                else:
                    elements.append(dl)
                defn_buffer.clear()
            expecting_defn_dd = False

        pre_buf: list[dict] = []  # raw blocks buffered while inside a gray rect

        def flush_pre_buf() -> None:
            nonlocal pre_buf
            if not pre_buf:
                return
            buf = pre_buf
            pre_buf = []
            if current_para is None:
                # Orphan pre block at the start of a page: create a bare ParagraphBlock
                # so _append_main can merge it with the previous page's trailing pre block.
                elements.append(ParagraphBlock("", 0, [self._extract_pre_multi(buf)]))
                return
            current_para.add(self._extract_pre_multi(buf))

        for block in blocks:
            # Flush buffered code-block lines when we leave a gray rect.
            if pre_buf and not self._is_in_gray_rect(block["bbox"]):
                flush_pre_buf()

            if self._is_heading_block(block):
                flush_ordered()
                flush_bullets()
                flush_defns()
                flush_para()
                headings = self._parse_headings(block, llmap)
                elements.extend(headings)
                if headings:
                    current_section_id = headings[-1].section_id

            elif self._is_unnumbered_heading_block(block):
                flush_ordered()
                flush_bullets()
                flush_defns()
                flush_para()
                text = "".join(s["text"] for ln in block["lines"] for s in ln["spans"]).strip()
                fixed_level = _UNNUMBERED_HEADING_LEVELS.get(text)
                if fixed_level is None:
                    cur_level = len(current_section_id.split(".")) if current_section_id else 1
                    fixed_level = cur_level + 1
                # Back-matter headings (Bibliography, Index) need an id for TOC anchors.
                sid = text if text in _UNNUMBERED_HEADING_IDS else ""
                elements.append(Heading(sid, text, fixed_level, subheading=True))

            elif self._is_forward_refs_block(block):
                flush_ordered()
                flush_bullets()
                flush_defns()
                flush_para()
                inlines = self._extract_forward_refs_inlines(block, llmap)
                elements.append(ForwardRefs(inlines))

            elif self._is_defn_block(block):
                flush_ordered()
                flush_bullets()
                term = self._extract_defn_dt_inlines(block, llmap)
                dd_inline = self._extract_defn_dd_from_block_tail(block, llmap)
                if dd_inline is not None:
                    defn_buffer.append(DefnItem(term, dd_inline))
                else:
                    defn_buffer.append(DefnItem(term, []))
                    expecting_defn_dd = True

            elif self._is_nested_bullet_block(block):
                # Nested (•) bullets: route to same buffer; flush_bullets handles nesting
                bullet_buffer.append((block, llmap))

            elif self._is_bullet_block(block):
                flush_ordered()
                flush_defns()
                bullet_buffer.append((block, llmap))

            elif self._is_ordered_item_block(block):
                if bullet_buffer:
                    flush_bullets()
                ordered_buffer.append(block)

            elif self._get_para_num(block) is not None:
                flush_ordered()
                flush_bullets()
                flush_defns()
                flush_para()
                para_num = self._get_para_num(block)
                assert para_num is not None
                para_id = f"{current_section_id}p{para_num}"
                current_para = _ParaBuilder(para_id, para_num)
                # Extract content lines (no para num span)
                lines = self._block_lines_without_para_num(block)
                if lines:
                    if self._all_mono_lines(lines):
                        current_para.add(self._extract_synopsis_pre(lines))
                    elif self._is_grammar_lines(lines):
                        for gb in self._extract_grammar_blocks(lines, llmap):
                            current_para.add(gb)
                    elif self._lines_start_with_bullet(lines):
                        # Para number sits on its own line; the content lines start with
                        # a bullet.  Route them through the bullet buffer so that any
                        # following bullet-only blocks are merged into the same <ul>.
                        bullet_buffer.append(({**block, "lines": lines}, llmap))
                    else:
                        inlines = self._process_lines_to_inlines(lines, llmap)
                        current_para.add(_TempProseBlock(inlines))

            elif self._is_in_gray_rect(block["bbox"]):
                # Code block (pre) — buffer until we leave the gray rect so that
                # indentation can be computed from the global baseline across all
                # blocks in this code example.
                if not pre_buf:
                    flush_bullets()
                    flush_defns()
                pre_buf.append(block)

            else:
                # Continuation block: prose or bridge between code blocks
                if expecting_defn_dd and defn_buffer:
                    lines = self._block_lines_without_para_num(block)
                    if lines:
                        dd_inlines = self._process_lines_to_inlines(lines, llmap)
                        defn_buffer[-1] = DefnItem(defn_buffer[-1].term, dd_inlines)
                    expecting_defn_dd = False
                else:
                    if ordered_buffer:
                        flush_ordered()
                    if bullet_buffer:
                        # Text after a bullet — part of the same list item? No: the
                        # standard puts each bullet in its own block. Flush.
                        flush_bullets()
                    lines = self._block_lines_without_para_num(block)
                    if lines:
                        if self._is_grammar_lines(lines):
                            if current_para is None:
                                # Annex A: grammar block with no paragraph number
                                current_para = _ParaBuilder("", 0)
                            for gb in self._extract_grammar_blocks(lines, llmap):
                                current_para.add(gb)
                        elif self._is_grammar_intro_line(lines):
                            # Annex A single-line term (e.g. "keyword: one of");
                            # productions follow in separate continuation blocks.
                            if current_para is None:
                                current_para = _ParaBuilder("", 0)
                            current_para.add(self._extract_grammar_block(lines, llmap))
                        elif self._is_grammar_continuation_lines(lines):
                            # Productions that either continue across a page boundary
                            # or follow a single-line Annex A term block.
                            prods = self._extract_grammar_block_productions(lines, llmap)
                            if (
                                current_para is not None
                                and current_para._raw
                                and isinstance(current_para._raw[-1], GrammarBlock)
                            ):
                                # Attach to the active grammar block (Annex A columns)
                                current_para._raw[-1].productions.extend(prods)
                            elif current_para is None:
                                # Orphan continuation across a page boundary
                                elements.append(ParagraphBlock("", 0, [GrammarBlock("", prods)]))
                            else:
                                # current_para exists but last child is not a grammar
                                # block — treat as prose (e.g. wrapped code lines).
                                inlines = self._process_lines_to_inlines(lines, llmap)
                                current_para.add(_TempProseBlock(inlines))
                        elif current_para is not None:
                            inlines = self._process_lines_to_inlines(lines, llmap)
                            current_para.add(_TempProseBlock(inlines))
                        else:
                            # Potential orphan OL item tail spilling from previous page
                            orphan_ol_continuation = self._process_lines_to_inlines(lines, llmap)

        flush_pre_buf()
        flush_ordered()
        flush_bullets()
        flush_defns()
        flush_para()
        return elements

    def _parse_footnotes(
        self,
        blocks: List[dict],
        llmap: Dict[float, List[dict]],
    ) -> List[Footnote]:
        footnotes = []
        for block in blocks:
            lines = block["lines"]
            if not lines:
                continue

            # A block may contain multiple footnotes (each starting with a small
            # superscript number like "7)" at the left margin).  Split on those.
            # fn_groups: list of (fn_num, lines_for_this_fn)
            fn_groups: List[Tuple[str, List[dict]]] = []

            for line in lines:
                spans = line["spans"]
                # Find whether this line starts with a footnote number span
                fn_num: Optional[str] = None
                fn_span_idx: Optional[int] = None
                for i, span in enumerate(spans):
                    if not span["text"].strip():
                        continue
                    # Only the first non-empty span can be the footnote number
                    if span["size"] < 7.5 and span["bbox"][0] < 100:
                        m = re.match(r"^(\d+)\)?$", span["text"].strip())
                        if m:
                            fn_num = m.group(1)
                            fn_span_idx = i
                    break

                if fn_num is not None:
                    # Strip the footnote-number span from this line
                    rest_spans = [s for j, s in enumerate(spans) if j != fn_span_idx]
                    new_line = {**line, "spans": rest_spans}
                    fn_groups.append((fn_num, [new_line] if rest_spans else []))
                elif fn_groups:
                    fn_groups[-1][1].append(line)

            for fn_num, fn_lines in fn_groups:
                fn_id = f"footnote-{fn_num}"
                inlines: List[Inline] = [SupNode(None, [TextNode(fn_num + ")")])]
                rest_inlines = self._process_lines_to_inlines(fn_lines, llmap)
                inlines.extend(rest_inlines)
                footnotes.append(Footnote(fn_id, inlines))

        return footnotes


# ---------------------------------------------------------------------------
# Helper: paragraph builder with bridge/prose classification
# ---------------------------------------------------------------------------


class _TempProseBlock:
    """Temporary marker for prose that may become ProseBlock or BridgeContent."""

    def __init__(self, inlines: List[Inline]) -> None:
        self.inlines = inlines


class _ParaBuilder:
    def __init__(self, para_id: str, num: int) -> None:
        self.para_id = para_id
        self.num = num
        self._raw: List[
            PreBlock | _TempProseBlock | BulletList | OrderedList | DefnList | GrammarBlock
        ] = []

    @property
    def para_id(self) -> str:
        return self._para_id

    @para_id.setter
    def para_id(self, v: str) -> None:
        self._para_id = v

    def add(
        self, item: PreBlock | _TempProseBlock | BulletList | OrderedList | DefnList | GrammarBlock
    ) -> None:
        if isinstance(item, PreBlock) and self._raw and isinstance(self._raw[-1], PreBlock):
            # Merge adjacent pre-blocks (separate PDF text blocks within one gray rect).
            # Preserve tokens from both sides: if either side has tokens, combine them;
            # a side without tokens is converted to a single PLAIN token.
            prev = self._raw[-1]
            merged_text = prev.text + "\n" + item.text
            if prev.tokens is not None or item.tokens is not None:
                prev_toks = prev.tokens if prev.tokens is not None else [PreToken(prev.text)]
                item_toks = item.tokens if item.tokens is not None else [PreToken(item.text)]
                self._raw[-1] = PreBlock(merged_text, prev_toks + [PreToken("\n")] + item_toks)
            else:
                self._raw[-1] = PreBlock(merged_text)
        else:
            self._raw.append(item)

    def build(self) -> ParagraphBlock:
        # Classify _TempProseBlock items as ProseBlock or BridgeContent
        children: List[ParagraphContent] = []
        raw = self._raw
        n = len(raw)
        for i, item in enumerate(raw):
            if isinstance(item, _TempProseBlock):
                # Bridge: comes after a PreBlock AND before a PreBlock
                prev_is_pre = i > 0 and isinstance(raw[i - 1], PreBlock)
                next_is_pre = i < n - 1 and isinstance(raw[i + 1], PreBlock)
                if prev_is_pre and next_is_pre:
                    children.append(BridgeContent(item.inlines))
                else:
                    children.append(ProseBlock(item.inlines))
            else:
                children.append(item)
        return ParagraphBlock(self._para_id, self.num, children)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def parse_page(pdf_path: str, pdf_page_index: int) -> Page:
    """
    Parse a page from the PDF at the given 0-based index.
    Returns a Page DOM object.
    """
    doc = fitz.open(pdf_path)
    page = doc[pdf_page_index]
    return PageParser(page).parse()


def parse_page_from_doc(doc: Any, pdf_page_index: int) -> Page:
    """Parse a page from an already-opened fitz document.

    Prefer this over parse_page() when iterating many pages, to avoid
    reopening the PDF on every call.
    """
    page = doc[pdf_page_index]
    return PageParser(page).parse()
