"""Shared pytest fixtures and command-line options."""

from __future__ import annotations

from typing import Any

# Module-level variable set by pytest_configure from the --pdf option.
_pdf_path: str = ""


def pytest_addoption(parser: Any) -> None:
    parser.addoption(
        "--pdf",
        required=True,
        help="Path to the PDF draft (e.g. n3685.pdf)",
    )


def pytest_configure(config: Any) -> None:
    global _pdf_path
    _pdf_path = config.getoption("--pdf")


def get_pdf_path() -> str:
    """Return the PDF path as configured by --pdf."""
    return _pdf_path
