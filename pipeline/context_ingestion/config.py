"""
Configuration and data source definitions for context ingestion.

All pipeline behavior is driven by Config and DATA_SOURCES.
Add new sources to DATA_SOURCES — no other code changes needed.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, List


@dataclass(frozen=True)
class Config:
    chroma_dir: str = "data/chroma_db/"
    collection_name: str = "housing_context"
    embedding_model_name: str = "BAAI/bge-small-en-v1.5"
    embedding_cache_dir: str = "~/.cache/homesignal/sentence_transformers"

    # Chunking
    min_chunk_words: int = 80
    max_chunk_words: int = 250

    # Fetching
    request_timeout: int = 30
    max_articles_per_source: int = 10
    delay_between_requests: float = 1.5

    # Deduplication similarity threshold (cosine distance)
    dedup_threshold: float = 0.05

    # Batch size for ChromaDB inserts
    batch_size: int = 64


DATA_SOURCES: List[Dict[str, Any]] = [
    {
        "name": "redfin_market_tracker",
        "type": "market_narrative",
        "base_urls": ["https://www.redfin.com/news/market-tracker/"],
        "topics": ["pricing", "inventory", "demand"],
        "geography": "national",
    },
    {
        "name": "zillow_research",
        "type": "market_narrative",
        "base_urls": ["https://www.zillow.com/research/"],
        "topics": ["pricing", "demand", "forecast"],
        "geography": "national",
    },
    {
        "name": "freddie_mac_rates",
        "type": "macro_context",
        "base_urls": ["https://www.freddiemac.com/pmms"],
        "topics": ["mortgage_rates"],
        "geography": "national",
    },
    {
        "name": "federal_reserve",
        "type": "macro_context",
        "base_urls": ["https://www.federalreserve.gov/newsevents.htm"],
        "topics": ["interest_rates", "inflation", "economy"],
        "geography": "national",
    },
    {
        "name": "bls_data",
        "type": "macro_context",
        "base_urls": ["https://www.bls.gov/news.release/"],
        "topics": ["inflation", "employment"],
        "geography": "national",
    },
    {
        "name": "census_housing",
        "type": "macro_context",
        "base_urls": ["https://www.census.gov/construction/nrc/index.html"],
        "topics": ["housing_supply", "construction"],
        "geography": "national",
    },
    {
        "name": "redfin_migration",
        "type": "market_narrative",
        "base_urls": [
            "https://www.redfin.com/news/category/housing-market/migration/"
        ],
        "topics": ["migration", "demand"],
        "geography": "national",
    },
]
