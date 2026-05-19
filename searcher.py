"""Search for device PDF URLs using Serper API + Claude classification."""

import json
import logging
import os
import re
from dataclasses import dataclass
from typing import Optional

import anthropic
import httpx

from config import CLAUDE_MODEL, USER_AGENT, SERPER_API_URL, SERPER_NUM_RESULTS
from shared import TokenUsage

logger = logging.getLogger(__name__)


@dataclass
class ResolveResult:
    brand: str
    description: str
    model_number: Optional[str] = None
    failure_reason: Optional[str] = None
    tokens: TokenUsage = None

    def __post_init__(self):
        if self.tokens is None:
            self.tokens = TokenUsage()


@dataclass
class SearchResult:
    device_name: str
    datasheet_url: Optional[str] = None
    manual_url: Optional[str] = None
    confidence: str = "none"
    notes: str = ""
    failure_reason: Optional[str] = None
    tokens: TokenUsage = None

    def __post_init__(self):
        if self.tokens is None:
            self.tokens = TokenUsage()


def _is_pdf_url(url: str) -> bool:
    """Check if URL points to a PDF file."""
    if not url:
        return False
    return url.lower().rstrip('/').endswith('.pdf')


def _extract_pdfs_from_html(html: str) -> list[str]:
    """Extract all PDF URLs from HTML using regex."""
    pdfs = re.findall(r'https?://[^\s"<>\']+\.pdf', html, re.IGNORECASE)
    return list(set(pdfs))  # dedupe


def _quick_match(pdf_urls: list[str], device_name: str) -> dict:
    """
    Fast path: device name in URL + obvious keyword in filename.
    Returns match with HIGH confidence, or None to fall through to Claude.
    """
    datasheet_keywords = ['datasheet', 'spec', 'ds_', '_ds.']
    manual_keywords = ['manual', 'user', 'guide', 'instruction']

    datasheet_url = None
    manual_url = None

    for url in pdf_urls:
        filename = url.lower().split('/')[-1]

        if device_name.lower() not in url.lower():
            continue

        is_datasheet = any(kw in filename for kw in datasheet_keywords)
        is_manual = any(kw in filename for kw in manual_keywords)

        if is_datasheet and not datasheet_url:
            datasheet_url = url
        elif is_manual and not manual_url:
            manual_url = url

    if datasheet_url or manual_url:
        return {'datasheet_url': datasheet_url, 'manual_url': manual_url, 'confidence': 'high'}

    return None  # Fall through to Claude


def _classify_with_claude(device_name: str, search_results: list[dict], tokens: TokenUsage = None) -> dict:
    """
    Ask Claude to pick the best datasheet/manual from search results.
    search_results: list of {'url': ..., 'title': ..., 'snippet': ...}
    """
    client = anthropic.Anthropic()

    results_text = "\n".join(
        f"{i+1}. {r['url']}\n   Title: {r.get('title', 'N/A')}\n   Snippet: {r.get('snippet', 'N/A')[:200]}"
        for i, r in enumerate(search_results)
    )

    prompt = f"""Find the official datasheet and user manual for: {device_name}

Search results:
{results_text}

Pick the URLs that are:
1. For this EXACT device (not a similar model)
2. Official documentation (datasheet, specs, manual)
3. Prefer official manufacturer or publisher URLs over third-party resellers, aggregators, or random sites. A manufacturer product page is better than a third-party PDF.
4. Only use English locale URLs. If an English URL is available, never pick a non-English one (e.g. avoid URLs with /de/, /es/, /fr/, /zh/, /ja/, non-English country codes, or non-English filenames like _es.pdf, _de.pdf, or non-English TLDs like .ro, .de, .fr when an English option exists). Only fall back to a non-English URL if absolutely no English option exists.

Respond with JSON only:
{{
    "datasheet_url": "URL or null if not found",
    "manual_url": "URL or null if not found",
    "confidence": "high/medium/low/none",
    "reason": "brief explanation"
}}

If unsure or no good match, set URLs to null and confidence to "none"."""

    try:
        response = client.messages.create(
            model=CLAUDE_MODEL,
            max_tokens=256,
            messages=[{"role": "user", "content": prompt}]
        )

        if tokens:
            tokens.add(response.usage)

        text = ""
        for block in response.content:
            if hasattr(block, "text"):
                text += block.text

        logger.debug("Classify response for %s:\n%s", device_name, text)

        json_match = re.search(r'\{[^{}]*\}', text, re.DOTALL)
        if json_match:
            result = json.loads(json_match.group())
            logger.info("Classified %s: ds=%s manual=%s confidence=%s",
                        device_name, result.get('datasheet_url'), result.get('manual_url'), result.get('confidence'))
            return result
        else:
            logger.warning("Could not parse classify response for %s", device_name)
            return {'datasheet_url': None, 'manual_url': None, 'confidence': 'none', 'reason': 'Could not parse Claude response'}

    except Exception as e:
        logger.error("Claude classify error for %s: %s", device_name, e)
        return {'datasheet_url': None, 'manual_url': None, 'confidence': 'none', 'reason': f'Claude error: {str(e)}'}


def _fetch_page(url: str) -> Optional[str]:
    """Fetch HTML content from a URL."""
    try:
        with httpx.Client(timeout=30, follow_redirects=True, headers={"User-Agent": USER_AGENT}) as client:
            response = client.get(url)
            if response.status_code == 200:
                return response.text
    except Exception:
        pass
    return None


def _serper_search(query: str, num_results: int = SERPER_NUM_RESULTS) -> list[dict]:
    """
    Search via Serper API (Google results).
    Returns list of {'url': ..., 'title': ..., 'snippet': ...}.
    Raises on API errors — no silent failures.
    """
    api_key = os.environ.get("SERPER_API_KEY")
    if not api_key:
        raise RuntimeError("SERPER_API_KEY not set in environment")

    with httpx.Client(timeout=15) as client:
        response = client.post(
            SERPER_API_URL,
            headers={"X-API-KEY": api_key, "Content-Type": "application/json"},
            json={"q": query, "num": num_results, "gl": "us", "hl": "en"},
        )
        response.raise_for_status()
        data = response.json()

    results = []
    for item in data.get("organic", []):
        results.append({
            "url": item.get("link", ""),
            "title": item.get("title", ""),
            "snippet": item.get("snippet", ""),
        })
    logger.debug("Serper '%s': %d results", query, len(results))
    for r in results:
        logger.debug("  %s — %s", r['url'], r['title'])
    return results


def _resolve_product_page(url: str, device_name: str, tokens: TokenUsage) -> dict:
    """
    Fetch a product page and try to find PDF links in its HTML.
    Returns dict with datasheet_url/manual_url or empty dict on failure.
    """
    logger.info("Resolving product page for %s: %s", device_name, url)
    html = _fetch_page(url)
    if not html:
        logger.debug("Failed to fetch product page: %s", url)
        return {}

    pdf_urls = _extract_pdfs_from_html(html)
    if not pdf_urls:
        logger.debug("No PDF links found on product page: %s", url)
        return {}

    logger.debug("Found %d PDF links on %s: %s", len(pdf_urls), url, pdf_urls)

    # Try quick match first
    quick = _quick_match(pdf_urls, device_name)
    if quick:
        logger.info("Quick match from product page for %s: %s", device_name, quick)
        return quick

    # Fall back to Claude classifying the PDF links
    pdf_results = [{'url': u, 'title': '', 'snippet': f'PDF link from {url}'} for u in pdf_urls]
    return _classify_with_claude(device_name, pdf_results, tokens)


def _merge_search_results(a: list[dict], b: list[dict]) -> list[dict]:
    """Merge two search result lists, deduplicating by URL."""
    seen = set()
    merged = []
    for r in a + b:
        if r['url'] not in seen:
            seen.add(r['url'])
            merged.append(r)
    return merged


def resolve_model_number(brand: str, description: str) -> ResolveResult:
    """
    Search for the exact model number of a device given its brand and description.
    Uses one Serper query + one Claude call to extract the model number.
    Only returns a result if confidence is high or medium.
    """
    tokens = TokenUsage()

    try:
        results = _serper_search(f"{brand} {description} model number")
        if not results:
            return ResolveResult(brand=brand, description=description,
                                 failure_reason="No search results", tokens=tokens)

        results_text = "\n".join(
            f"{i+1}. {r['url']}\n   Title: {r.get('title', 'N/A')}\n   Snippet: {r.get('snippet', 'N/A')[:200]}"
            for i, r in enumerate(results)
        )

        prompt = f"""Find the exact model number for this product: {brand} {description}

Search results:
{results_text}

Only return a model number if you are highly confident it matches "{description}" from {brand}.
Do not guess. If multiple models could match or you are uncertain, return null.

Respond with JSON only:
{{"model_number": "EXACT-MODEL or null", "confidence": "high/medium/low", "reason": "brief explanation"}}"""

        client = anthropic.Anthropic()
        response = client.messages.create(
            model=CLAUDE_MODEL,
            max_tokens=256,
            messages=[{"role": "user", "content": prompt}]
        )
        tokens.add(response.usage)

        text = "".join(block.text for block in response.content if hasattr(block, "text"))
        logger.debug("Resolve response for %s %s:\n%s", brand, description, text)

        start = text.find('{')
        end = text.rfind('}')
        if start == -1 or end == -1:
            return ResolveResult(brand=brand, description=description,
                                 failure_reason="Could not parse response", tokens=tokens)

        result = json.loads(text[start:end + 1])
        model = result.get("model_number")
        confidence = result.get("confidence", "low")
        reason = result.get("reason", "")

        if model and confidence in ("high", "medium"):
            logger.info("Resolved %s %s → %s (%s)", brand, description, model, confidence)
            return ResolveResult(brand=brand, description=description,
                                 model_number=model, tokens=tokens)
        else:
            return ResolveResult(brand=brand, description=description,
                                 failure_reason=f"Low confidence: {reason}", tokens=tokens)

    except Exception as e:
        return ResolveResult(brand=brand, description=description,
                             failure_reason=f"Error: {str(e)}", tokens=tokens)


def search_pdf_urls(device_name: str) -> SearchResult:
    """
    Search for PDFs using Serper API (Google results).
    Two searches (datasheet + manual), merged results, one Claude classification.
    1. Quick match: device name in URL + keyword → HIGH confidence, no Claude
    2. Otherwise: Claude classifies from merged results
    3. No fallbacks - if unsure, fail explicitly
    """
    tokens = TokenUsage()

    try:
        ds_results = _serper_search(f"{device_name} datasheet PDF")
        manual_results = _serper_search(f"{device_name} user manual PDF")
        raw_results = _merge_search_results(ds_results, manual_results)

        if not raw_results:
            return SearchResult(
                device_name=device_name,
                failure_reason="No search results found",
                tokens=tokens
            )

        direct_pdfs = [r['url'] for r in raw_results if _is_pdf_url(r['url'])]

        if direct_pdfs:
            quick = _quick_match(direct_pdfs, device_name)
            if quick:
                return SearchResult(
                    device_name=device_name,
                    datasheet_url=quick.get('datasheet_url'),
                    manual_url=quick.get('manual_url'),
                    confidence='high',
                    notes="Matched by URL pattern",
                    tokens=tokens
                )

        classified = _classify_with_claude(device_name, raw_results, tokens)

        datasheet = classified.get('datasheet_url')
        manual = classified.get('manual_url')
        confidence = classified.get('confidence', 'none')
        reason = classified.get('reason', '')

        if confidence == 'none':
            return SearchResult(
                device_name=device_name,
                failure_reason=f"Claude unsure: {reason}",
                tokens=tokens
            )

        valid_datasheet = datasheet if datasheet and _is_pdf_url(datasheet) else None
        valid_manual = manual if manual and _is_pdf_url(manual) else None

        non_pdf_urls = []
        if datasheet and not valid_datasheet:
            non_pdf_urls.append(datasheet)
        if manual and not valid_manual and manual != datasheet:
            non_pdf_urls.append(manual)

        if non_pdf_urls and (not valid_datasheet or not valid_manual):
            for page_url in non_pdf_urls:
                resolved = _resolve_product_page(page_url, device_name, tokens)
                if not valid_datasheet and resolved.get('datasheet_url') and _is_pdf_url(resolved['datasheet_url']):
                    valid_datasheet = resolved['datasheet_url']
                if not valid_manual and resolved.get('manual_url') and _is_pdf_url(resolved['manual_url']):
                    valid_manual = resolved['manual_url']

        if not valid_datasheet and not valid_manual:
            # Fallback: try Claude classifying just the direct PDFs from search results
            if direct_pdfs:
                logger.info("Product page resolution failed for %s, falling back to direct PDFs", device_name)
                pdf_results = [{'url': u, 'title': '', 'snippet': 'Direct PDF from search results'} for u in direct_pdfs]
                fallback = _classify_with_claude(device_name, pdf_results, tokens)
                fb_ds = fallback.get('datasheet_url')
                fb_manual = fallback.get('manual_url')
                fb_confidence = fallback.get('confidence', 'none')
                if fb_confidence != 'none':
                    if fb_ds and _is_pdf_url(fb_ds):
                        valid_datasheet = fb_ds
                    if fb_manual and _is_pdf_url(fb_manual):
                        valid_manual = fb_manual

            if not valid_datasheet and not valid_manual:
                non_pdf = datasheet or manual
                if non_pdf:
                    return SearchResult(
                        device_name=device_name,
                        failure_reason=f"No PDF found (product page: {non_pdf})",
                        tokens=tokens
                    )
                return SearchResult(
                    device_name=device_name,
                    failure_reason=f"No matching PDFs found: {reason}",
                    tokens=tokens
                )

        return SearchResult(
            device_name=device_name,
            datasheet_url=valid_datasheet,
            manual_url=valid_manual,
            confidence=confidence,
            notes=reason,
            tokens=tokens
        )

    except Exception as e:
        return SearchResult(
            device_name=device_name,
            failure_reason=f"Search error: {str(e)}",
            tokens=tokens
        )


