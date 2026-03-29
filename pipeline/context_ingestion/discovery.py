"""
Step 1: Discovery — find article/report URLs from base pages.

Crawls each base_url, extracts same-domain links that look like articles,
and returns up to max_articles unique URLs. Avoids pagination loops and
non-content pages.
"""

from __future__ import annotations

from typing import List, Optional
from urllib.parse import urljoin, urlparse

import requests
from bs4 import BeautifulSoup


_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
}

_SKIP_PATTERNS = [
    "/page/", "/tag/", "/author/", "#", ".pdf", ".zip",
    ".jpg", ".png", ".gif", "/feed", "/rss", "/login",
    "/signup", "/subscribe",
]


def fetch_page(url: str, timeout: int = 30) -> Optional[str]:
    """Fetch a page and return HTML string, or None on failure."""
    try:
        resp = requests.get(url, headers=_HEADERS, timeout=timeout, allow_redirects=True)
        resp.raise_for_status()
        return resp.text
    except Exception as e:
        print(f"  WARN: Failed to fetch {url}: {e}")
        return None


def discover_article_urls(
    base_url: str,
    max_articles: int,
    timeout: int,
) -> List[str]:
    """
    Discover article/report links from a base URL page.
    Returns up to max_articles unique URLs.
    """
    html = fetch_page(base_url, timeout)
    if not html:
        return []

    soup = BeautifulSoup(html, "lxml")
    base_domain = urlparse(base_url).netloc

    candidates: List[str] = []
    seen: set = set()

    for a_tag in soup.find_all("a", href=True):
        href = a_tag["href"]
        full_url = urljoin(base_url, href)
        parsed = urlparse(full_url)

        # Stay on same domain
        if parsed.netloc != base_domain:
            continue

        # Skip non-article patterns
        path = parsed.path.rstrip("/")
        if not path or path == urlparse(base_url).path.rstrip("/"):
            continue

        if any(pat in full_url.lower() for pat in _SKIP_PATTERNS):
            continue

        # Path should have depth (at least 2 segments) to look like an article
        if path.count("/") < 2:
            continue

        clean_url = f"{parsed.scheme}://{parsed.netloc}{parsed.path}"
        if clean_url not in seen:
            seen.add(clean_url)
            candidates.append(clean_url)

    # If no deep links found, treat the base URL itself as content
    if not candidates:
        candidates = [base_url]

    return candidates[:max_articles]
