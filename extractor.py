"""PDF text and metadata extraction using pdfplumber."""

from dataclasses import dataclass
from pathlib import Path
from typing import Optional

import pdfplumber


@dataclass
class PageData:
    page_number: int
    text: str
    has_images: bool
    has_tables: bool
    char_count: int
    is_likely_scanned: bool  # Has images but no text


@dataclass
class PDFMetadata:
    total_pages: int
    title: Optional[str] = None
    author: Optional[str] = None
    creator: Optional[str] = None


def extract_page_data(pdf_path: Path) -> tuple[list[PageData], PDFMetadata]:
    """
    Extract text and metadata from each page of a PDF.

    Args:
        pdf_path: Path to the PDF file

    Returns:
        Tuple of (list of PageData, PDFMetadata)
    """
    pages = []

    with pdfplumber.open(pdf_path) as pdf:
        metadata = PDFMetadata(
            total_pages=len(pdf.pages),
            title=pdf.metadata.get("Title"),
            author=pdf.metadata.get("Author"),
            creator=pdf.metadata.get("Creator")
        )

        for i, page in enumerate(pdf.pages):
            text = page.extract_text() or ""
            char_count = len(text.strip())

            # Check for images
            has_images = len(page.images) > 0

            # Check for tables
            tables = page.extract_tables()
            has_tables = len(tables) > 0

            # A page is likely scanned if it has images but very little text
            is_likely_scanned = has_images and char_count < 50

            pages.append(PageData(
                page_number=i + 1,  # 1-indexed
                text=text,
                has_images=has_images,
                has_tables=has_tables,
                char_count=char_count,
                is_likely_scanned=is_likely_scanned
            ))

    return pages, metadata


def format_pages_for_evaluation(pages: list[PageData], max_chars_per_page: int = 1000) -> str:
    """
    Format extracted pages for Claude evaluation.

    Args:
        pages: List of PageData objects
        max_chars_per_page: Maximum characters to include per page

    Returns:
        Formatted string for evaluation prompt
    """
    formatted = []

    for page in pages:
        # Truncate text if too long
        text = page.text[:max_chars_per_page]
        if len(page.text) > max_chars_per_page:
            text += "... [truncated]"

        # Add metadata indicators
        indicators = []
        if page.has_images:
            indicators.append("HAS_IMAGES")
        if page.has_tables:
            indicators.append("HAS_TABLES")
        if page.is_likely_scanned:
            indicators.append("LIKELY_SCANNED")
        if page.char_count < 100:
            indicators.append("LOW_TEXT")

        indicator_str = f" [{', '.join(indicators)}]" if indicators else ""

        formatted.append(f"--- PAGE {page.page_number}{indicator_str} ---\n{text}\n")

    return "\n".join(formatted)


def get_page_summary(pages: list[PageData]) -> dict:
    """Get summary statistics about extracted pages."""
    total_chars = sum(p.char_count for p in pages)
    pages_with_images = sum(1 for p in pages if p.has_images)
    pages_with_tables = sum(1 for p in pages if p.has_tables)
    likely_scanned = sum(1 for p in pages if p.is_likely_scanned)
    low_text_pages = sum(1 for p in pages if p.char_count < 100)

    return {
        "total_pages": len(pages),
        "total_chars": total_chars,
        "avg_chars_per_page": total_chars // len(pages) if pages else 0,
        "pages_with_images": pages_with_images,
        "pages_with_tables": pages_with_tables,
        "likely_scanned_pages": likely_scanned,
        "low_text_pages": low_text_pages
    }
