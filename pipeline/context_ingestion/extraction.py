"""
Steps 2 & 3: Extraction + Normalization.

Parses raw HTML into clean, structured ExtractedArticle objects.
Extracts title, publish date, and main body text.
Removes ads, navigation, sidebars, and other non-content elements.
Normalizes whitespace while preserving numbers exactly.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import List, Optional

from bs4 import BeautifulSoup
from urllib.parse import urlparse


@dataclass
class ExtractedArticle:
    url: str
    title: str
    publish_date: str
    content: str  # cleaned plain text


def normalize_date(raw: str) -> str:
    """Parse a date string into YYYY-MM-DD format."""
    raw = raw.strip()
    for fmt in [
        "%Y-%m-%dT%H:%M:%S%z",
        "%Y-%m-%dT%H:%M:%SZ",
        "%Y-%m-%dT%H:%M:%S",
        "%Y-%m-%d",
        "%B %d, %Y",
        "%b %d, %Y",
        "%m/%d/%Y",
    ]:
        try:
            return datetime.strptime(raw[:25], fmt).strftime("%Y-%m-%d")
        except ValueError:
            continue
    match = re.search(r"(\d{4}-\d{2}-\d{2})", raw)
    if match:
        return match.group(1)
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


def _extract_publish_date(soup: BeautifulSoup) -> str:
    """Try to extract publish date from common meta tags and elements."""
    for attr in ["article:published_time", "datePublished", "date", "DC.date.issued"]:
        tag = soup.find("meta", attrs={"property": attr}) or soup.find(
            "meta", attrs={"name": attr}
        )
        if tag and tag.get("content"):
            return normalize_date(tag["content"])

    time_tag = soup.find("time")
    if time_tag:
        dt = time_tag.get("datetime") or time_tag.get_text(strip=True)
        if dt:
            return normalize_date(dt)

    for script in soup.find_all("script", type="application/ld+json"):
        try:
            data = json.loads(script.string or "")
            if isinstance(data, list):
                data = data[0] if data else {}
            for key in ["datePublished", "dateCreated", "dateModified"]:
                if key in data:
                    return normalize_date(data[key])
        except (json.JSONDecodeError, TypeError):
            continue

    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


def extract_article(url: str, html: str) -> Optional[ExtractedArticle]:
    """Extract title, date, and main content from an HTML page."""
    soup = BeautifulSoup(html, "lxml")

    # Remove non-content elements
    for tag_name in ["nav", "header", "footer", "aside", "script", "style", "noscript"]:
        for el in soup.find_all(tag_name):
            el.decompose()
    for el in soup.find_all(class_=re.compile(
        r"(ad|ads|advert|sidebar|nav|menu|footer|comment|share|social|related|popup)",
        re.IGNORECASE,
    )):
        el.decompose()

    # Title
    title = ""
    h1 = soup.find("h1")
    if h1:
        title = h1.get_text(strip=True)
    if not title:
        og = soup.find("meta", attrs={"property": "og:title"})
        if og and og.get("content"):
            title = og["content"]
    if not title:
        title_tag = soup.find("title")
        if title_tag:
            title = title_tag.get_text(strip=True)
    if not title:
        title = urlparse(url).path.strip("/").split("/")[-1].replace("-", " ").title()

    # Publish date
    publish_date = _extract_publish_date(soup)

    # Main content — try <article>, then <main>, then <body>
    content_root = soup.find("article") or soup.find("main") or soup.find("body")
    if not content_root:
        return None

    paragraphs: List[str] = []
    for el in content_root.find_all(["p", "h2", "h3", "h4", "li"]):
        text = el.get_text(separator=" ", strip=True)
        if len(text) > 30:
            text = re.sub(r"\s+", " ", text)
            paragraphs.append(text)

    content = "\n\n".join(paragraphs)

    if len(content.split()) < 50:
        return None

    return ExtractedArticle(
        url=url,
        title=title,
        publish_date=publish_date,
        content=content,
    )
