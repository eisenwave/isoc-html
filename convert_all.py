#!/usr/bin/env python3
"""Convert every PDF in the current directory to an HTML file.

Each PDF is converted to a single-file HTML document placed next to the
source PDF with the same stem and a ``.html`` extension.

Usage:
    python convert_all.py [directory]

<directory> defaults to the current working directory.  PDFs that already
have a corresponding up-to-date HTML file are silently skipped (pass
``--force`` to override).
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

# Allow running from the project root without installing the package.
sys.path.insert(0, str(Path(__file__).parent))

from src.document import parse_document
from src.html_serializer import serialize_document


def convert(pdf_path: Path, force: bool = False) -> Path:
    """Convert *pdf_path* to HTML and return the output path.

    If the HTML file already exists and is newer than the PDF, the
    conversion is skipped unless *force* is True.
    """
    out_path = pdf_path.with_suffix(".html")

    if not force and out_path.exists():
        if out_path.stat().st_mtime >= pdf_path.stat().st_mtime:
            print(f"  skip  {pdf_path.name}  (HTML is up to date)")
            return out_path

    print(f"  conv  {pdf_path.name}  →  {out_path.name}", flush=True)
    doc = parse_document(str(pdf_path))
    html = serialize_document(doc.sections, doc.footnotes, doc.draft_id)
    out_path.write_bytes(html.encode("utf-8"))
    return out_path


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "directory",
        nargs="?",
        default=".",
        help="Directory to search for PDFs (default: current directory).",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Reconvert even if the HTML file is already up to date.",
    )
    args = parser.parse_args()

    directory = Path(args.directory).resolve()
    if not directory.is_dir():
        sys.exit(f"error: not a directory: {directory}")

    pdfs = sorted(directory.glob("*.pdf"))
    if not pdfs:
        print(f"No PDF files found in {directory}")
        return

    print(f"Converting {len(pdfs)} PDF(s) in {directory}")
    errors: list[tuple[Path, Exception]] = []
    for pdf in pdfs:
        try:
            convert(pdf, force=args.force)
        except Exception as exc:
            print(f"  ERROR {pdf.name}: {exc}", file=sys.stderr)
            errors.append((pdf, exc))

    if errors:
        sys.exit(f"\n{len(errors)} conversion(s) failed.")
    print("Done.")


if __name__ == "__main__":
    main()
