# isoc-html

Converts a C-standard PDF draft (ISO/IEC N3685) into
a single navigable HTML document with syntax-highlighted code blocks,
cross-linked grammar productions,
and stable anchor links to standard-library declarations.

---

## Requirements

- **Python 3.12+**
- The PDF file `n3685.pdf` placed in the project root
  (not included in the repository)

---

## Setup

Create and activate a virtual environment,
then install all dependencies:

```bash
python3 -m venv .venv
source .venv/bin/activate           # on Windows: .venv\Scripts\activate
pip install pymupdf beautifulsoup4 pytest ruff pyright lxml Pygments
```

| Package | Purpose |
|---------|---------|
| `pymupdf` | Extract text blocks and layout from PDF pages |
| `beautifulsoup4` | Parse HTML in tests and the deserialiser |
| `lxml` | HTML parser backend for BeautifulSoup |
| `Pygments` | (Available; not currently used directly) |
| `pytest` | Test runner |
| `ruff` | Linter and formatter |
| `pyright` | Static type checker (strict mode) |

---

## Quick Start

Generate the full HTML document:

```bash
.venv/bin/python3 generate.py n3685.pdf > out.html
```

Convert a single PDF page (e.g. PDF page 30 = document page 13):

```bash
.venv/bin/python3 convert_page.py n3685.pdf 30
```

---

## Running Tests

```bash
.venv/bin/pytest tests/ -q
```

---

## Linting and Formatting

```bash
.venv/bin/ruff check src/ tests/       # lint
.venv/bin/ruff format src/ tests/ *.py # format
```

---

## Type Checking

```bash
.venv/bin/python3 -m pyright src/ tests/test_document.py
```

---

See [`copilot-instructions.md`](copilot-instructions.md) for a full technical
overview of the codebase.
