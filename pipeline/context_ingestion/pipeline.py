"""
Core pipeline orchestrator.

Ties together all processing steps for a single source:
  discovery → extraction → chunking → enrichment → summarization → dedup

Produces ProcessedChunk objects ready for vectorstore insertion.
"""

from __future__ import annotations

import json
import time
import uuid
from typing import Any, Dict, List

from .config import Config
from .discovery import discover_article_urls, fetch_page
from .extraction import extract_article
from .chunking import semantic_chunk
from .enrichment import extract_signals, extract_drivers, extract_metrics_mentioned, infer_topic
from .summarization import summarize_chunk
from .models import ProcessedChunk


def _content_hash_inline(text: str) -> str:
    """Inline hash to avoid circular import with vectorstore."""
    import hashlib
    import re
    normalized = re.sub(r"\s+", " ", text.lower().strip())
    return hashlib.sha256(normalized.encode()).hexdigest()[:16]


def process_source(
    source_cfg: Dict[str, Any],
    cfg: Config,
) -> List[ProcessedChunk]:
    """Process a single data source end-to-end. Returns list of enriched chunks."""
    name = source_cfg["name"]
    doc_type = source_cfg["type"]
    topics = source_cfg.get("topics", [])
    geography = source_cfg.get("geography", "national")
    base_urls = source_cfg.get("base_urls", [])

    print(f"\n--- Processing source: {name} ---")

    all_chunks: List[ProcessedChunk] = []
    seen_hashes: set = set()

    for base_url in base_urls:
        # Step 1: Discovery
        article_urls = discover_article_urls(
            base_url, cfg.max_articles_per_source, cfg.request_timeout
        )
        print(f"  Discovered {len(article_urls)} URLs from {base_url}")

        for url in article_urls:
            time.sleep(cfg.delay_between_requests)

            # Step 2: Extraction
            html = fetch_page(url, cfg.request_timeout)
            if not html:
                continue

            article = extract_article(url, html)
            if not article:
                print(f"  SKIP (insufficient content): {url}")
                continue

            print(f"  Extracted: {article.title[:60]}...")

            # Step 4: Semantic chunking
            chunks = semantic_chunk(
                article.content, cfg.min_chunk_words, cfg.max_chunk_words
            )

            for chunk_text in chunks:
                # Dedup within this run
                h = _content_hash_inline(chunk_text)
                if h in seen_hashes:
                    continue
                seen_hashes.add(h)

                # Step 5: Enrichment
                signals = extract_signals(chunk_text)
                drivers = extract_drivers(chunk_text)
                metrics = extract_metrics_mentioned(chunk_text)
                topic = infer_topic(chunk_text, topics)

                # Step 6: Summarization
                summary_data = summarize_chunk(chunk_text)

                doc_id = str(uuid.uuid4())

                metadata = {
                    "source": name,
                    "url": article.url,
                    "title": article.title,
                    "date": article.publish_date,
                    "document_type": doc_type,
                    "topic": topic,
                    "geography": geography,
                    "metrics": json.dumps(metrics),
                    "signals": json.dumps(signals),
                    "drivers": json.dumps(drivers),
                    "summary": summary_data["summary"],
                    "key_points": json.dumps(summary_data["key_points"]),
                    "content_hash": h,
                }

                all_chunks.append(ProcessedChunk(
                    document_id=doc_id,
                    chunk_text=chunk_text,
                    metadata=metadata,
                ))

    print(f"  Source {name}: {len(all_chunks)} chunks produced")
    return all_chunks
