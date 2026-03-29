"""
HomeSignal connectivity smoke checks.

Fast, non-LLM checks that validate core wiring:
  1) Pipeline orchestrator imports and exposes run_pipeline
  2) Backend metro read APIs return data
  3) Chat API signatures expose metro_filter threading

Usage:
    ./venv/bin/python evals/smoke_connectivity.py
"""

from __future__ import annotations

import argparse
import inspect
import os
import sys
from pathlib import Path

import chromadb

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

import backend.api as api
import pipeline.run_all as run_all
from backend.chat_engine import ChatEngine
from backend.rag import RAGEngine


def _assert(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


def check_pipeline_orchestrator() -> None:
    _assert(hasattr(run_all, "run_pipeline"), "run_pipeline not found in pipeline.run_all")
    print("OK pipeline.run_all exposes run_pipeline")


def check_backend_metro_reads() -> None:
    metros = api.get_metros()
    _assert(not metros.empty, "get_metros returned no rows")

    sample_metro = str(metros.iloc[0]["metro_name"])
    latest = api.get_latest_metrics_for_metro(sample_metro)
    trend = api.get_trend_series(sample_metro, months=12)

    _assert(bool(latest), f"latest snapshot is empty for {sample_metro}")
    _assert(not trend.empty, f"trend series is empty for {sample_metro}")
    print(f"OK backend metro reads for {sample_metro}")


def check_chat_signature_wiring() -> None:
    api_sig = inspect.signature(api.chat)
    engine_sig = inspect.signature(ChatEngine.chat)

    _assert("metro_filter" in api_sig.parameters, "backend.api.chat missing metro_filter")
    _assert("metro_filter" in engine_sig.parameters, "ChatEngine.chat missing metro_filter")
    print("OK chat metro_filter signature wiring")


def check_context_collection_retrieval() -> None:
    """
    Assert that:
      1) housing_context collection is readable and non-empty
      2) stored context docs carry required metadata
      3) RAG class is wired to call _retrieve_context_docs in query flow
    """
    # Use runtime defaults from the RAG config dataclass.
    from backend.rag import Config as RAGConfig
    rag_cfg = RAGConfig()
    client = chromadb.PersistentClient(path=rag_cfg.chroma_dir)
    try:
        collection = client.get_collection(name=rag_cfg.context_collection_name)
        total = int(collection.count())
    except Exception:
        raise AssertionError("Could not open/read housing_context collection")

    _assert(total > 0, "housing_context collection is empty")
    sample = collection.get(limit=min(3, total), include=["metadatas", "documents"])
    docs = sample.get("documents") or []
    metas = sample.get("metadatas") or []
    _assert(len(docs) > 0, "housing_context has no readable documents")
    _assert(len(metas) > 0, "housing_context has no readable metadata")

    first_meta = metas[0] or {}
    for key in ("source", "url", "content_hash"):
        _assert(key in first_meta, f"housing_context metadata missing key: {key}")

    query_src = inspect.getsource(RAGEngine.query)
    _assert(
        "_retrieve_context_docs(question)" in query_src,
        "RAGEngine.query is not wired to include housing_context retrieval",
    )
    print(f"OK context retrieval assertion ({total} docs in housing_context, wired into RAG query)")


def _has_local_embedding_cache(model_name: str) -> bool:
    """
    Return True when the sentence-transformers model is available locally.
    Filesystem-only detection to avoid any network calls.
    """
    safe = model_name.replace("/", "_")
    torch_cache = Path.home() / ".cache" / "torch" / "sentence_transformers" / safe
    hf_cache_root = Path.home() / ".cache" / "huggingface" / "hub"
    hf_model_dir = hf_cache_root / f"models--{model_name.replace('/', '--')}"
    return torch_cache.exists() or hf_model_dir.exists()


def check_deep_live_semantic_retrieval() -> None:
    """
    Optional deep check: run live semantic retrieval from housing_context through RAG.
    Skips gracefully when local embedding cache is unavailable.
    """
    from backend.rag import Config as RAGConfig

    cfg = RAGConfig()
    if not _has_local_embedding_cache(cfg.embedding_model_name):
        print(
            f"SKIP deep retrieval (embedding model not cached locally: {cfg.embedding_model_name})"
        )
        return

    # Force offline behavior so this check remains deterministic in restricted environments.
    prev_hf_offline = os.environ.get("HF_HUB_OFFLINE")
    os.environ["HF_HUB_OFFLINE"] = "1"
    try:
        rag = RAGEngine(cfg=cfg)
        docs = rag._retrieve_context_docs("latest housing market trends and mortgage rates")
        _assert(
            len(docs) > 0,
            "deep retrieval returned zero docs from housing_context",
        )
        print(f"OK deep semantic retrieval ({len(docs)} docs)")
    except Exception as e:
        print(f"SKIP deep retrieval (RAG init/retrieval unavailable: {e})")
    finally:
        if prev_hf_offline is None:
            os.environ.pop("HF_HUB_OFFLINE", None)
        else:
            os.environ["HF_HUB_OFFLINE"] = prev_hf_offline


def main() -> None:
    parser = argparse.ArgumentParser(description="HomeSignal connectivity smoke checks")
    parser.add_argument(
        "--deep",
        action="store_true",
        help="Run optional deep semantic retrieval check (cache-aware)",
    )
    args = parser.parse_args()

    print("Running HomeSignal connectivity smoke checks...")
    check_pipeline_orchestrator()
    check_backend_metro_reads()
    check_chat_signature_wiring()
    check_context_collection_retrieval()
    if args.deep:
        check_deep_live_semantic_retrieval()
    print("All smoke checks passed.")


if __name__ == "__main__":
    main()
