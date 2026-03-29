"""
context_ingestion — Config-driven pipeline for ingesting external housing
market content into ChromaDB for RAG retrieval.

Submodules:
    config        — Config dataclass + DATA_SOURCES definitions
    discovery     — URL discovery from base pages
    extraction    — HTML parsing, article extraction, date normalization
    chunking      — Semantic text chunking (80–250 words)
    enrichment    — Signal, driver, metric extraction + topic inference
    summarization — Extractive summarization (no LLM)
    vectorstore   — ChromaDB embedding, dedup, batch insertion
    pipeline      — Per-source orchestrator (ProcessedChunk)

Usage:
    python -m pipeline.context_ingestion              # run full ingestion
    python -m pipeline.context_ingestion --help       # see options

    from pipeline.context_ingestion import main
    main()                                            # programmatic
    main(sources=[...])                               # custom sources
"""

from __future__ import annotations

import time
from typing import Any, Dict, List, Optional

from dotenv import load_dotenv

from .config import Config, DATA_SOURCES
from .models import ProcessedChunk
from .pipeline import process_source
from .vectorstore import (
    SentenceTransformerEmbeddingFunction,
    get_or_create_collection,
    deduplicate_against_existing,
    insert_chunks,
)


def main(sources: Optional[List[Dict[str, Any]]] = None) -> None:
    """Run the full context ingestion pipeline."""
    load_dotenv()
    cfg = Config()
    sources = sources or DATA_SOURCES

    print("\n=== HomeSignal Context Ingestion ===")
    print(f"Sources to process: {len(sources)}")
    print(f"Collection: {cfg.collection_name}")
    print(f"ChromaDB dir: {cfg.chroma_dir}")

    embedding_fn = SentenceTransformerEmbeddingFunction(cfg.embedding_model_name)
    collection = get_or_create_collection(cfg, embedding_fn)

    total_inserted = 0
    source_stats: List[Dict[str, Any]] = []
    start_time = time.time()

    for source_cfg in sources:
        name = source_cfg["name"]
        try:
            chunks = process_source(source_cfg, cfg)
            chunks = deduplicate_against_existing(collection, chunks)
            inserted = insert_chunks(collection, chunks, cfg.batch_size)
            total_inserted += inserted
            source_stats.append({"name": name, "chunks": inserted, "status": "ok"})
        except Exception as e:
            print(f"  ERROR processing {name}: {e}")
            source_stats.append({"name": name, "chunks": 0, "status": f"error: {e}"})

    elapsed = time.time() - start_time

    print("\n=== Ingestion Summary ===")
    print(f"Total chunks inserted: {total_inserted}")
    print(f"Elapsed: {elapsed:.1f}s")
    for stat in source_stats:
        status = "OK" if stat["status"] == "ok" else stat["status"]
        print(f"  {stat['name']}: {stat['chunks']} chunks ({status})")

    try:
        count = collection.count()
        print(f"\nCollection '{cfg.collection_name}' total documents: {count}")
    except Exception:
        pass

    print("\nContext ingestion complete.")


__all__ = [
    "main",
    "Config",
    "DATA_SOURCES",
    "ProcessedChunk",
    "process_source",
]
