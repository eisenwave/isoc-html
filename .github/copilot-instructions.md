# isoc-html — Copilot Instructions

These instructions explain the project for an AI coding agent.
Markdown documents in this project use
[semantic line breaks](https://sembr.org)
(one sentence or clause per line).
Please follow that convention when editing any `.md` file here.

---

## What This Project Does

`isoc-html` converts a C-standard PDF draft (N3685)
into a single navigable HTML document
plus a set of individual golden page files.
It does this by extracting text blocks from the PDF with PyMuPDF,
classifying them into a typed AST,
cross-linking grammar productions and standard-library declarations,
and serialising to HTML with inline syntax highlighting.

---

## Repository Layout

```
isoc-html/
├── src/                    # library modules (importable as `src.*`)
│   ├── dom.py              # AST dataclasses + TokenKind enum
│   ├── pdf_parser.py       # PDF → Page (single-page parse)
│   ├── document.py         # multi-page merge → Document (sections + footnotes)
│   ├── html_serializer.py  # Page / Document → HTML string
│   ├── html_deserializer.py # HTML string → Page AST (used by tests)
│   ├── normalize.py        # helper for round-trip comparison in tests
│   └── style.css           # embedded stylesheet
├── tests/
│   ├── test_pages.py       # golden-page mapping + round-trip tests (pages/<draft>/*.html)
│   └── test_document.py    # structural / content tests on the full document
├── pages/                  # golden HTML snapshots, one subdirectory per draft
│   ├── n3685/              # e.g. 1.html, abstract-1.html, …
│   └── n3220/              # e.g. 74.html
├── generate.py             # CLI: PDF → full document HTML (stdout)
├── convert_page.py         # CLI: PDF + page-number → pretty-printed page HTML
├── regenerate_pages.py     # CLI: update all pages/<draft>/*.html from a PDF
├── pyproject.toml          # ruff configuration
└── pyrightconfig.json      # pyright configuration (strict mode)
```

The PDF `n3685.pdf` must be present in the project root
but is **not** committed (it is in `.gitignore`).
Other drafts (e.g. `n3220.pdf`) can be added the same way.

---

## Virtual Environment

All tools live in `.venv`.
Always prefix commands with `.venv/bin/` or activate first:

```bash
python3 -m venv .venv
.venv/bin/pip install pymupdf beautifulsoup4 pytest ruff pyright lxml Pygments
```

Python 3.12+ is required.

---

## Running Tests

```bash
.venv/bin/pytest tests/ --pdf n3685.pdf -q
```

The ``--pdf`` option is required — always specify which PDF draft to test.
The ``--pdf`` value also selects which draft's golden pages are tested:
`--pdf n3685.pdf` exercises `pages/n3685/`, `--pdf n3220.pdf` exercises
`pages/n3220/`, and so on.

Expected: **476 passed** (for n3685.pdf).
The test suite reads the PDF and parses the whole document,
so it requires the PDF to be present.

`test_pages.py` does round-trip tests:
it discovers every golden HTML page under `pages/<draft>/`,
parses each one with the HTML deserialiser,
parses the same page from the PDF with the PDF parser,
and asserts the two ASTs are equivalent after normalisation.
The stem → PDF-page mapping is defined in `test_pages.py`
(`build_stem_to_pdf_index`) and is derived from the PDF's page footers,
so no front-matter offsets are hard-coded.

`test_document.py` builds the full merged document
(exactly as `generate.py` does)
and runs structural checks on the resulting HTML with BeautifulSoup.
Among them, `test_no_dangling_section_references` verifies that every
section reference (heading self-links and prose cross-references) resolves
to an existing element id — this passes for both `n3685.pdf` and `n3220.pdf`.

Note that a few `test_document.py` checks are N3685-content-specific
(they assert on particular paragraph numbers or a particular PDF page),
so not every test is expected to pass for every draft.

To run only the document tests:

```bash
.venv/bin/pytest tests/test_document.py --pdf n3685.pdf -q
```

---

## Linting

```bash
.venv/bin/ruff check src/ tests/
```

Rules enabled: `E` (pycodestyle errors), `W` (warnings),
`F` (Pyflakes — unused imports / undefined names),
`I` (isort import order).
`E501` (line-too-long) is disabled; the formatter handles line length instead.

Fix automatically:

```bash
.venv/bin/ruff check --fix src/ tests/
```

---

## Formatting

```bash
.venv/bin/ruff format src/ tests/ *.py
```

Settings (in `pyproject.toml`): 100-character line limit,
double quotes, space indentation.

Check without writing:

```bash
.venv/bin/ruff format --check src/ tests/ *.py
```

---

## Type Checking

```bash
.venv/bin/python3 -m pyright src/ tests/test_document.py
```

Mode: strict (`pyrightconfig.json`).
Target: **0 errors, 0 warnings**.
`reportUnusedImport` and `reportUnusedVariable` are set to `information`
(shown but not treated as errors).

---

## Generating Output

### Full document

```bash
.venv/bin/python3 generate.py n3685.pdf > out.html
```

Produces a single self-contained HTML file on stdout.
The document is structured as:

```html
<html>
  <head>…<style>…</style></head>
  <body>
    <main>
      <section id="section-{slug}">…</section>
      …
      <section id="section-footnotes">…</section>
    </main>
  </body>
</html>
```

Each `<section>` corresponds to one ISO clause
(grouped by the `current_clause` footer value on each PDF page).
Slugs are lower-cased, non-alphanumeric characters replaced with `-`.

### All PDFs in the current directory

```bash
.venv/bin/python3 convert_all.py [directory]
```

Converts every `*.pdf` in `directory` (default: `.`) to a same-named `.html` file.
PDFs whose HTML is already newer than the source are skipped.
Pass `--force` to reconvert unconditionally.

### Single page (debug / development)

```bash
.venv/bin/python3 convert_page.py n3685.pdf <1-based-pdf-page>
```

For example, document page 13 is PDF page 30:

```bash
.venv/bin/python3 convert_page.py n3685.pdf 30
```

---

## Updating Golden Pages

When the serialiser output changes intentionally, regenerate all golden files
for a draft (defaults to `n3685.pdf`):

```bash
.venv/bin/python3 regenerate_pages.py n3685.pdf
```

The script rewrites every `*.html` under `pages/<draft>/`
and derives the stem → PDF-page mapping from the PDF's footers,
so it works unchanged for any draft:

```bash
.venv/bin/python3 regenerate_pages.py n3220.pdf
```

Or regenerate a single page by running `convert_page.py` and redirecting
(the page number is the 1-based PDF page; `pages/<draft>/N.html` holds
document page N, whose PDF offset depends on the draft's front matter):

```bash
.venv/bin/python3 convert_page.py n3685.pdf 30 > pages/n3685/13.html
.venv/bin/python3 convert_page.py n3220.pdf 87 > pages/n3220/74.html
```

Never commit or stage any golden page changes.

---

## Key Source Modules

### `src/dom.py` — AST types

The central `Page` dataclass holds:
- `header`, `footer` — `Header`/`Footer` with clause/section text
- `main: List[MainElement]` — the page body
- `footnotes: List[Footnote]`

`MainElement = Union[Heading, ParagraphBlock, ISOCoverBlock]`

`ParagraphBlock.children: List[ParagraphContent]`
where `ParagraphContent = Union[Paragraph, PreBlock, List_]`

`PreBlock` holds either raw `text: str`
or a list of typed `tokens: List[PreToken]`.
`PreToken(text, kind)` where `kind` is a `TokenKind` enum value
(e.g. `FUNCTION`, `KEYWORD`, `PLACEHOLDER`, `TYPENAME`, …).

`SectionBlock(slug, elements)` groups `MainElement`s in the full document.

`Document(sections: List[SectionBlock], footnotes: List[Footnote])`

### `src/document.py` — multi-page merge

`parse_document(pdf_path) -> Document`
parses every page, merges cross-page list/code-block continuations,
applies grammar cross-linking,
and groups pages into `SectionBlock`s by `current_clause` footer value.

`_clause_to_slug(clause)` lowercases and replaces non-alphanumeric runs with `-`.

### `tests/test_pages.py` — golden-page mapping

The stem → PDF-page mapping lives in `tests/test_pages.py`
(`build_stem_to_pdf_index`, `golden_pages_dir`, `stem_sort_key`)
rather than in a separate module,
because it is only needed by the golden-page tests.
It is derived from the page footers at runtime:
- `abstract-N` / `contents-N` / `foreword-N` / `introduction-N`
  map to the N-th page of that section (identified by the footer clause);
- `N` (a document page number) maps to the page whose footer page number is `N`.

Because the mapping comes from the footers,
no front-matter offsets are hard-coded
and the same code works for any draft.
The `regenerate_pages.py` CLI embeds its own copy of the mapping
so it can run without importing the test suite.

### `src/pdf_parser.py` — section references

PDF links carry named destinations such as `section.7.27` or
`subsection.0.5.2.1`.
`_nameddest_to_href` maps both the newer `keyword.0.…` format
(recent drafts like N3685)
and the older `keyword.…` format
(C23 / N3220) to a plain section fragment (`#7.27`),
so cross-references like `7.27` are never split into `7.` plus a
dangling link to `#27`.
Non-section destinations (`page.N`, `Item.N`, `lstnumber.N.M`, …)
map to `#` and are dropped.

Three further `PageParser` rules keep references resolvable for any draft:
- `_find_footnote_sep_y` ignores horizontal rules shorter than 50pt,
  so fraction bars in math never push real body content into the footnote
  region;
- `_is_heading_block` / `_parse_headings` accept headings merged by
  PyMuPDF with the following content into one block,
  re-dispatching the leftover lines as ordinary content;
- `_find_link_split` refuses to match a bare-digit section reference
  inside a larger number (e.g. the `15` of `2015`).

### `src/html_serializer.py` — rendering

`serialize(page) -> str` renders a single `Page` to minimal HTML.

`serialize_document(sections, footnotes) -> str`
renders the full `Document` to a complete `<html>` document.

Both call `_serialize_main(elements)`,
which detects `Synopsis` subheadings
and passes `in_synopsis=True` to the following `ParagraphBlock`
so that function-declaration anchors are injected.

**Declaration anchors:**
In a synopsis `PreBlock`, any non-whitespace `FUNCTION` token
immediately followed by a `PLAIN` token starting with `(`
is rendered as:
```html
<a id="decl-{name}" href="#decl-{name}"><span class="tk-fn">{name}</span></a>
```
This makes every standard-library function name in a Synopsis
a stable, self-referential link target.

`prettify(html) -> str` wraps BeautifulSoup to produce indented output
for golden files and `convert_page.py`.

### `src/html_deserializer.py` — round-trip

`parse_html(html_string) -> Page`
reconstructs a `Page` AST from serialised HTML.
Used exclusively by `test_pages.py` for golden-file comparisons.

---

## Coding Conventions

- Strict Pyright — all code must type-check at zero errors.
- Ruff lint + format — run before committing.
- `from __future__ import annotations` in every source file.
- Public functions get docstrings; private helpers (`_name`) do not require them.
- Tests use `unittest.TestCase`; no pytest fixtures.
- Golden pages are updated by running `regenerate_pages.py`, never by hand.
- Never duplicate the synopsis-detection logic;
  it lives exclusively in `_serialize_main`.
