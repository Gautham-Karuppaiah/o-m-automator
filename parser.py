"""Parse device names from arbitrary file formats using Claude."""

import base64
import json
import logging
from pathlib import Path

import anthropic
import pdfplumber

from config import CLAUDE_MODEL, DEVICE_PARSE_PROMPT
from shared import TokenUsage

logger = logging.getLogger(__name__)

TEXT_EXTENSIONS = {'.txt', '.csv', '.tsv', '.md', '.log', '.json', '.xml', '.html'}
IMAGE_EXTENSIONS = {'.png', '.jpg', '.jpeg', '.gif', '.bmp', '.webp'}
PDF_EXTENSIONS = {'.pdf'}
SUPPORTED_EXTENSIONS = TEXT_EXTENSIONS | IMAGE_EXTENSIONS | PDF_EXTENSIONS

IMAGE_MEDIA_TYPES = {
    '.png': 'image/png',
    '.jpg': 'image/jpeg',
    '.jpeg': 'image/jpeg',
    '.gif': 'image/gif',
    '.bmp': 'image/bmp',
    '.webp': 'image/webp',
}


def _read_text_file(file_path: Path) -> str:
    """Read a text-based file and return its content."""
    return file_path.read_text(encoding='utf-8', errors='replace')


def _read_pdf_file(file_path: Path) -> str:
    """Extract text from a PDF file using pdfplumber."""
    pages = []
    with pdfplumber.open(file_path) as pdf:
        for page in pdf.pages:
            text = page.extract_text() or ""
            if text.strip():
                pages.append(text)
    if not pages:
        raise ValueError(f"No extractable text found in PDF: {file_path}")
    return "\n\n".join(pages)


def _transcribe_image(file_path: Path, client: anthropic.Anthropic, tokens: TokenUsage) -> str:
    """
    Pass 1 for images: pure OCR transcription.
    Ask Claude to repeat every line of text verbatim — no interpretation.
    """
    ext = file_path.suffix.lower()
    media_type = IMAGE_MEDIA_TYPES[ext]
    data = base64.standard_b64encode(file_path.read_bytes()).decode('utf-8')

    response = client.messages.create(
        model=CLAUDE_MODEL,
        max_tokens=4096,
        messages=[{"role": "user", "content": [
            {
                "type": "image",
                "source": {"type": "base64", "media_type": media_type, "data": data},
            },
            {
                "type": "text",
                "text": (
                    "Transcribe every line of text visible in this image exactly as written. "
                    "Do not interpret, classify, summarize, or add anything. "
                    "Repeat the text row by row, preserving brand names, model numbers, descriptions, and quantities exactly as they appear. "
                    "IMPORTANT: Do not insert spaces into model numbers or part codes. "
                    "Alphanumeric codes like 'ELPEC01', 'GS728TPP', 'MX418D/C' must be transcribed without added spaces."
                ),
            },
        ]}],
    )
    tokens.add(response.usage)

    transcript = "".join(block.text for block in response.content if hasattr(block, "text"))
    logger.debug("Transcription for %s:\n%s", file_path.name, transcript)
    return transcript


def parse_devices_from_file(file_path: Path) -> tuple[list[str], list[str], list[dict], TokenUsage]:
    """
    Extract device model numbers from any supported file.

    For images: two-pass approach — first transcribe verbatim, then classify from transcript.
    For text/PDF: single pass directly against the text content.

    Returns:
        devices: list of valid device model names
        skipped: list of generic/vague entries that were filtered out
        unresolved: list of dicts with 'brand' and 'description' for items without a model number
        tokens: API token usage for cost tracking
    """
    tokens = TokenUsage()
    ext = file_path.suffix.lower()

    if ext not in SUPPORTED_EXTENSIONS:
        supported = ', '.join(sorted(SUPPORTED_EXTENSIONS))
        raise ValueError(f"Unsupported file type '{ext}'. Supported: {supported}")

    client = anthropic.Anthropic()

    # Build text content to classify
    if ext in IMAGE_EXTENSIONS:
        # Pass 1: transcribe image verbatim
        text = _transcribe_image(file_path, client, tokens)
        content = DEVICE_PARSE_PROMPT.format(content=text)
    else:
        if ext in PDF_EXTENSIONS:
            text = _read_pdf_file(file_path)
        else:
            text = _read_text_file(file_path)
        content = DEVICE_PARSE_PROMPT.format(content=text)

    # Pass 2 (or only pass for non-images): classify
    response = client.messages.create(
        model=CLAUDE_MODEL,
        max_tokens=4096,
        messages=[{"role": "user", "content": content}],
    )
    tokens.add(response.usage)

    # Parse response
    text = ""
    for block in response.content:
        if hasattr(block, "text"):
            text += block.text

    logger.debug("Parse response for %s:\n%s", file_path.name, text)

    # Extract outermost JSON object (handles nested arrays/objects)
    start = text.find('{')
    end = text.rfind('}')
    if start == -1 or end == -1:
        raise ValueError(f"Could not parse Claude response: {text[:200]}")

    parsed = json.loads(text[start:end + 1])
    devices = parsed.get("devices", [])
    skipped = parsed.get("skipped", [])
    unresolved = parsed.get("unresolved", [])

    if not devices and not unresolved:
        raise ValueError("No device names extracted from file")

    return devices, skipped, unresolved, tokens
