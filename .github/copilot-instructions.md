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
│   ├── test_pages.py       # golden-file round-trip tests (one per pages/*.html)
│   └── test_document.py    # structural / content tests on the full document
├── pages/                  # golden HTML snapshots (committed)
├── generate.py             # CLI: PDF → full document HTML (stdout)
├── convert_page.py         # CLI: PDF + page-number → pretty-printed page HTML
├── regenerate_pages.py     # CLI: update all pages/*.html from PDF
├── pyproject.toml          # ruff configuration
└── pyrightconfig.json      # pyright configuration (strict mode)
```

The PDF `n3685.pdf` must be present in the project root
but is **not** committed (it is in `.gitignore`).

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
.venv/bin/pytest tests/ -q
```

Expected: **433 passed**.
The test suite reads `n3685.pdf` and parses the whole document,
so it requires the PDF to be present.

`test_pages.py` does round-trip tests:
it parses each `pages/*.html` with the HTML deserialiser,
parses the same page from the PDF with the PDF parser,
and asserts the two ASTs are equivalent after normalisation.

`test_document.py` builds the full merged document
(exactly as `generate.py` does)
and runs structural checks on the resulting HTML with BeautifulSoup.

To run only the document tests:

```bash
.venv/bin/pytest tests/test_document.py -q
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

When the serialiser output changes intentionally, regenerate all golden files:

```bash
.venv/bin/python3 regenerate_pages.py
```

Or regenerate a single page by running `convert_page.py` and redirecting:

```bash
.venv/bin/python3 convert_page.py n3685.pdf 30 > pages/13.html
```

Page naming convention:

| File name         | PDF pages (1-based) |
|-------------------|---------------------|
| `abstract-N.html` | N                   |
| `contents-N.html` | N + 4               |
| `foreword-N.html` | N + 14              |
| `introduction-N.html` | N + 15          |
| `N.html`          | N + 17              |

After regenerating, commit the updated `pages/*.html` files.

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
