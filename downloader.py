"""PDF downloader with retry logic and validation."""

import logging
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

import httpx

from config import DOWNLOAD_TIMEOUT, MAX_DOWNLOAD_RETRIES, USER_AGENT

logger = logging.getLogger(__name__)


@dataclass
class DownloadResult:
    url: str
    output_path: Optional[Path] = None
    success: bool = False
    file_size: int = 0
    failure_reason: Optional[str] = None


def _is_valid_pdf(content: bytes) -> bool:
    return content[:4] == b'%PDF'


def _detect_login_wall(content: bytes, headers: dict) -> bool:
    if "text/html" in headers.get("content-type", "").lower():
        return True
    content_lower = content[:2000].lower()
    indicators = [b"login", b"sign in", b"captcha", b"access denied", b"unauthorized"]
    return any(ind in content_lower for ind in indicators)


def download_pdf(url: str, output_path: Path) -> DownloadResult:
    last_error = None

    with httpx.Client(timeout=DOWNLOAD_TIMEOUT, follow_redirects=True, headers={"User-Agent": USER_AGENT}) as client:
        for attempt in range(MAX_DOWNLOAD_RETRIES):
            try:
                logger.debug("Download attempt %d/%d: %s", attempt + 1, MAX_DOWNLOAD_RETRIES, url)
                response = client.get(url)

                if response.status_code != 200:
                    last_error = f"HTTP {response.status_code}"
                    logger.warning("HTTP %d for %s", response.status_code, url)
                    if response.status_code in (401, 403):
                        return DownloadResult(url=url, failure_reason=f"Access denied (HTTP {response.status_code})")
                    continue

                content = response.content
                logger.debug("Downloaded %d bytes, content-type: %s", len(content), response.headers.get("content-type", ""))

                if _detect_login_wall(content, dict(response.headers)):
                    logger.warning("Login wall detected for %s", url)
                    return DownloadResult(url=url, failure_reason="Login wall or CAPTCHA detected")

                if not _is_valid_pdf(content):
                    logger.warning("Not a valid PDF: %s (first bytes: %s)", url, content[:20])
                    return DownloadResult(url=url, failure_reason="Downloaded content is not a valid PDF")

                output_path.parent.mkdir(parents=True, exist_ok=True)
                output_path.write_bytes(content)
                logger.info("Saved %d bytes to %s", len(content), output_path)

                return DownloadResult(url=url, output_path=output_path, success=True, file_size=len(content))

            except httpx.TimeoutException:
                last_error = "Timeout"
                logger.warning("Timeout on attempt %d for %s", attempt + 1, url)
                time.sleep(2 ** attempt)
            except httpx.RequestError as e:
                last_error = str(e)
                logger.warning("Request error on attempt %d for %s: %s", attempt + 1, url, e)
                time.sleep(2 ** attempt)

    logger.error("Download failed after %d attempts: %s — %s", MAX_DOWNLOAD_RETRIES, url, last_error)
    return DownloadResult(url=url, failure_reason=f"Failed after {MAX_DOWNLOAD_RETRIES} attempts: {last_error}")
