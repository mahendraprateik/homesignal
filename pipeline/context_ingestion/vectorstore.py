"""
Steps 7 & 8: ChromaDB operations — embedding, insertion, deduplication.

Manages the "housing_context" ChromaDB collection.
Embeds chunks using the shared SentenceTransformer model,
deduplicates against existing content, and batch-inserts new documents.
"""

from __future__ import annotations

import os
from typing import List

import chromadb
from sentence_transformers import SentenceTransformer

from .config import Config
from .models import ProcessedChunk


# ---------------------------------------------------------------------------
# Embedding function (matches update_vectors.py)
# ---------------------------------------------------------------------------

class SentenceTransformerEmbeddingFunction:
    def __init__(
        self,
        model_name: str,
        cache_dir: str,
    ) -> None:
        self._model_name = model_name
        self._cache_dir = os.path.expanduser(cache_dir)
        os.makedirs(self._cache_dir, exist_ok=True)
        self._model = SentenceTransformer(
            model_name,
            cache_folder=self._cache_dir,
        )

    def __call__(self, input: List[str]) -> List[List[float]]:
        embeddings = self._model.encode(
            input,
            show_progress_bar=False,
            convert_to_numpy=True,
            normalize_embeddings=True,
        )
        return embeddings.tolist()


# ---------------------------------------------------------------------------
# Collection management
# ---------------------------------------------------------------------------

def get_or_create_collection(
    cfg: Config,
    embedding_fn: SentenceTransformerEmbeddingFunction,
) -> chromadb.Collection:
    """Get or create the housing_context collection (does NOT clear existing data)."""
    os.makedirs(os.path.dirname(os.path.abspath(cfg.chroma_dir)) or ".", exist_ok=True)
    client = chromadb.PersistentClient(path=cfg.chroma_dir)
    return client.get_or_create_collection(
        name=cfg.collection_name,
        embedding_function=embedding_fn,
    )


# ---------------------------------------------------------------------------
# Deduplication
# ---------------------------------------------------------------------------

def deduplicate_against_existing(
    collection: chromadb.Collection,
    chunks: List[ProcessedChunk],
) -> List[ProcessedChunk]:
    """Remove chunks whose content_hash already exists in the collection."""
    if not chunks:
        return []

    new_hashes = {c.metadata["content_hash"] for c in chunks}

    try:
        existing = collection.get(
            where={"content_hash": {"$in": list(new_hashes)}},
            include=["metadatas"],
        )
        existing_hashes = {
            m.get("content_hash") for m in (existing.get("metadatas") or []) if m
        }
    except Exception:
        existing_hashes = set()

    before = len(chunks)
    filtered = [c for c in chunks if c.metadata["content_hash"] not in existing_hashes]
    skipped = before - len(filtered)
    if skipped:
        print(f"  Dedup: skipped {skipped} chunks already in collection")
    return filtered


# ---------------------------------------------------------------------------
# Batch insertion
# ---------------------------------------------------------------------------

def insert_chunks(
    collection: chromadb.Collection,
    chunks: List[ProcessedChunk],
    batch_size: int,
) -> int:
    """Insert chunks into ChromaDB in batches. Returns count inserted."""
    if not chunks:
        return 0

    total = len(chunks)
    for start in range(0, total, batch_size):
        end = min(start + batch_size, total)
        batch = chunks[start:end]

        collection.add(
            ids=[c.document_id for c in batch],
            documents=[c.chunk_text for c in batch],
            metadatas=[c.metadata for c in batch],
        )
        print(f"  Inserted {end}/{total} chunks...")

    return total
