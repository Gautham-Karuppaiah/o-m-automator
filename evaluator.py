"""Claude Haiku page evaluation for filtering PDF content."""

import json
import logging
import re
from dataclasses import dataclass
from typing import Optional

import anthropic

from config import CLAUDE_MODEL, EVALUATION_PROMPT, MAX_PAGES_PER_BATCH
from extractor import PageData, format_pages_for_evaluation
from shared import TokenUsage

logger = logging.getLogger(__name__)


@dataclass
class PageDecision:
    page_number: int
    decision: str  # KEEP, REMOVE, or FLAG
    reason: str


@dataclass
class EvaluationResult:
    decisions: list[PageDecision]
    tokens: TokenUsage
    failure_reason: Optional[str] = None


def evaluate_pages(
    pages: list[PageData],
    device_names: str | list[str],
    doc_type: str = "datasheet",
    client: anthropic.Anthropic = None
) -> EvaluationResult:
    """
    Evaluate pages and decide KEEP/REMOVE/FLAG for each.

    Args:
        pages: List of PageData from extractor
        device_names: Single device name or list of device names (for grouped evaluation)
        doc_type: "datasheet" or "manual"
        client: Optional Anthropic client

    Returns:
        EvaluationResult with decisions for each page
    """
    if isinstance(device_names, str):
        device_names = [device_names]

    if client is None:
        client = anthropic.Anthropic()

    tokens = TokenUsage()
    all_decisions = []

    # Process in batches to avoid token limits
    for i in range(0, len(pages), MAX_PAGES_PER_BATCH):
        batch = pages[i:i + MAX_PAGES_PER_BATCH]
        batch_decisions = _evaluate_batch(batch, device_names, doc_type, client, tokens)
        all_decisions.extend(batch_decisions)

    return EvaluationResult(decisions=all_decisions, tokens=tokens)


def _evaluate_batch(
    pages: list[PageData],
    device_names: list[str],
    doc_type: str,
    client: anthropic.Anthropic,
    tokens: TokenUsage
) -> list[PageDecision]:
    """Evaluate a batch of pages."""
    pages_content = format_pages_for_evaluation(pages)

    prompt = EVALUATION_PROMPT.format(
        device_names=", ".join(device_names),
        doc_type=doc_type,
        pages_content=pages_content
    )

    try:
        response = client.messages.create(
            model=CLAUDE_MODEL,
            max_tokens=2048,
            messages=[{"role": "user", "content": prompt}]
        )

        tokens.add(response.usage)

        text = ""
        for block in response.content:
            if hasattr(block, "text"):
                text += block.text

        logger.debug("Evaluation response for %s (pages %d-%d):\n%s",
                      ", ".join(device_names), pages[0].page_number, pages[-1].page_number, text)

        decisions = _parse_decisions(text, pages)
        for d in decisions:
            logger.info("Page %d: %s — %s", d.page_number, d.decision, d.reason)
        return decisions

    except Exception as e:
        logger.error("Evaluation API error for %s: %s", ", ".join(device_names), e)
        # On error, FLAG all pages with error reason
        return [
            PageDecision(page_number=p.page_number, decision="FLAG", reason=f"API error: {e}")
            for p in pages
        ]


def _parse_decisions(text: str, pages: list[PageData]) -> list[PageDecision]:
    """Parse Claude's JSON response into PageDecision objects."""
    decisions = []

    # Try to extract JSON array
    json_match = re.search(r'\[[\s\S]*\]', text)

    if json_match:
        try:
            data = json.loads(json_match.group())
            for item in data:
                page_num = item.get("page", item.get("page_number"))
                decision = item.get("decision", "KEEP").upper()
                reason = item.get("reason", "")

                # Validate decision
                if decision not in ("KEEP", "REMOVE", "FLAG"):
                    decision = "FLAG"  # Invalid response, flag for review

                decisions.append(PageDecision(
                    page_number=page_num,
                    decision=decision,
                    reason=reason
                ))
            return decisions
        except json.JSONDecodeError:
            pass

    # Fallback: FLAG all pages if parse failed
    return [
        PageDecision(page_number=p.page_number, decision="FLAG", reason="Could not parse Claude response")
        for p in pages
    ]


def get_decision_summary(decisions: list[PageDecision]) -> dict:
    """Get summary statistics for decisions."""
    keep = sum(1 for d in decisions if d.decision == "KEEP")
    remove = sum(1 for d in decisions if d.decision == "REMOVE")
    flag = sum(1 for d in decisions if d.decision == "FLAG")

    return {
        "total": len(decisions),
        "keep": keep,
        "remove": remove,
        "flag": flag,
        "keep_pages": [d.page_number for d in decisions if d.decision == "KEEP"],
        "remove_pages": [d.page_number for d in decisions if d.decision == "REMOVE"],
        "flag_pages": [d.page_number for d in decisions if d.decision == "FLAG"]
    }
