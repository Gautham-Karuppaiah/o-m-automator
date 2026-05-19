"""Combine KEEP+FLAG pages from all evaluated PDFs into a single output PDF."""

import logging
from dataclasses import dataclass
from pathlib import Path

from pypdf import PdfReader, PdfWriter

from evaluator import PageDecision

logger = logging.getLogger(__name__)


@dataclass
class GroupPages:
    """Pages and decisions for one device group."""
    pdf_path: Path
    decisions: list[PageDecision]
    device_names: list[str]
    doc_type: str = "datasheet"


@dataclass
class CombineResult:
    output_path: Path
    total_pages_included: int
    total_pages_removed: int
    total_pages_flagged: int
    total_original_pages: int


def write_filtered_pdf(
    pdf_path: Path,
    decisions: list[PageDecision],
    output_path: Path,
) -> CombineResult:
    """
    Write KEEP+FLAG pages from a single PDF to output_path.
    Adds [FLAG] bookmarks for flagged pages.
    """
    reader = PdfReader(pdf_path)
    writer = PdfWriter()

    keep_decisions = sorted(
        [d for d in decisions if d.decision in ("KEEP", "FLAG")],
        key=lambda d: d.page_number,
    )
    flag_decisions = [d for d in decisions if d.decision == "FLAG"]
    removed = len([d for d in decisions if d.decision == "REMOVE"])

    orig_to_out = {}
    for decision in keep_decisions:
        orig_idx = decision.page_number - 1
        if 0 <= orig_idx < len(reader.pages):
            writer.add_page(reader.pages[orig_idx])
            orig_to_out[decision.page_number] = len(writer.pages) - 1

    for d in flag_decisions:
        if d.page_number in orig_to_out:
            writer.add_outline_item(
                f"[FLAG] p{d.page_number}: {d.reason}",
                orig_to_out[d.page_number],
            )

    output_path.parent.mkdir(parents=True, exist_ok=True)
    with open(output_path, "wb") as f:
        writer.write(f)

    logger.info("Wrote %d/%d pages to %s", len(orig_to_out), len(reader.pages), output_path)

    return CombineResult(
        output_path=output_path,
        total_pages_included=len(orig_to_out),
        total_pages_removed=removed,
        total_pages_flagged=len(flag_decisions),
        total_original_pages=len(reader.pages),
    )


def combine_filtered_pdfs(
    groups: list[GroupPages],
    output_path: Path,
) -> CombineResult:
    """
    Combine KEEP+FLAG pages from multiple PDFs into a single output PDF.

    Bookmarks:
      - One top-level bookmark per device group → first page of that group
      - Nested [FLAG] bookmarks under the group for flagged pages

    Args:
        groups: List of GroupPages from each processed device group
        output_path: Where to save the combined PDF

    Returns:
        CombineResult with counts and output path
    """
    writer = PdfWriter()
    total_kept = 0
    total_removed = 0
    total_flagged = 0
    total_original = 0

    for group in groups:
        group_label = ", ".join(group.device_names)
        logger.info("Combining %s (%s): %s", group_label, group.doc_type, group.pdf_path)

        reader = PdfReader(group.pdf_path)
        total_original += len(reader.pages)

        keep_decisions = sorted(
            [d for d in group.decisions if d.decision in ("KEEP", "FLAG")],
            key=lambda d: d.page_number,
        )
        flag_decisions = [d for d in group.decisions if d.decision == "FLAG"]
        removed_decisions = [d for d in group.decisions if d.decision == "REMOVE"]
        removed = len(removed_decisions)

        logger.info("  %d pages: %d included, %d removed, %d flagged",
                     len(reader.pages), len(keep_decisions), removed, len(flag_decisions))
        for d in removed_decisions:
            logger.debug("  REMOVE p%d: %s", d.page_number, d.reason)

        total_removed += removed
        total_flagged += len(flag_decisions)

        # Track where this group's pages land in the combined PDF
        group_start = len(writer.pages)
        orig_to_combined = {}

        for decision in keep_decisions:
            orig_idx = decision.page_number - 1  # 1-indexed → 0-indexed
            if 0 <= orig_idx < len(reader.pages):
                writer.add_page(reader.pages[orig_idx])
                orig_to_combined[decision.page_number] = len(writer.pages) - 1

        pages_added = len(orig_to_combined)
        total_kept += pages_added

        if pages_added == 0:
            continue

        # Top-level bookmark: "Device1, Device2 (datasheet)" or "(manual)"
        group_label = ", ".join(group.device_names)
        bookmark_title = f"{group_label} ({group.doc_type})"
        parent = writer.add_outline_item(bookmark_title, group_start)

        # Nested FLAG bookmarks under the group
        for d in flag_decisions:
            if d.page_number in orig_to_combined:
                title = f"[FLAG] p{d.page_number}: {d.reason}"
                writer.add_outline_item(title, orig_to_combined[d.page_number], parent=parent)

    output_path.parent.mkdir(parents=True, exist_ok=True)
    with open(output_path, "wb") as f:
        writer.write(f)

    return CombineResult(
        output_path=output_path,
        total_pages_included=total_kept,
        total_pages_removed=total_removed,
        total_pages_flagged=total_flagged,
        total_original_pages=total_original,
    )
