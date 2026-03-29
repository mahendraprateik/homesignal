"""
Build and persist the ChromaDB vector store for HomeSignal RAG.

Inputs:
- SQLite: data/homesignal.db
  - Table redfin_metrics (metro monthly metrics; period_date = YYYY-MM-01)

FRED data is NOT embedded — it is structured/numerical and queried directly
from SQLite at RAG query time by the RAGEngine.

Outputs:
- ChromaDB persisted at data/chroma_db/
- Collection name: housing_market
- Collection is cleared and rebuilt on every run.

Document rules:
- One "market_data" document per (metro_name, state, period_date month)
  containing Redfin housing metrics only.
- One "metric_definition" document containing metric definitions (YAML-like).

Metadata attached to every document:
- metro_name
- state
- period_date
- doc_type ("market_data" or "metric_definition")
"""

from __future__ import annotations

import os
import re
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Dict, List, Tuple

import chromadb
import pandas as pd
from dotenv import load_dotenv
from sentence_transformers import SentenceTransformer

from backend.semantic_model import get_semantic_model


@dataclass(frozen=True)
class Config:
    db_path: str = "data/homesignal.db"
    chroma_dir: str = "data/chroma_db/"
    collection_name: str = "housing_market"

    redfin_table: str = "redfin_metrics"

    embedding_model_name: str = "BAAI/bge-small-en-v1.5"
    embedding_cache_dir: str = "~/.cache/homesignal/sentence_transformers"
    chroma_add_batch_size: int = 16

    # Progress logging
    log_every: int = 25




class SentenceTransformerEmbeddingFunction:
    """
    Minimal adapter so Chroma can call a local sentence-transformers model.
    """

    def __init__(
        self,
        model_name: str,
        cache_dir: str,
    ) -> None:
        self.model_name = model_name
        self.cache_dir = os.path.expanduser(cache_dir)
        os.makedirs(self.cache_dir, exist_ok=True)
        self.model = SentenceTransformer(
            model_name,
            cache_folder=self.cache_dir,
        )

    def __call__(self, input: List[str]) -> List[List[float]]:
        embeddings = self.model.encode(
            input,
            show_progress_bar=False,
            convert_to_numpy=True,
            normalize_embeddings=True,
        )
        return embeddings.tolist()


def _print_header(step: str) -> None:
    print(f"\n=== {step} ===")


def _ensure_parent_dir(path: str) -> None:
    parent = os.path.dirname(os.path.abspath(path))
    if parent and not os.path.exists(parent):
        os.makedirs(parent, exist_ok=True)


def _safe_id(s: str) -> str:
    s = s.strip()
    s = s.replace(" ", "_")
    s = re.sub(r"[^a-zA-Z0-9_.:-]+", "_", s)
    return s


def _fmt(x: Any, digits: int = 2) -> str:
    """Format a possibly-null float for embedding text."""
    if x is None:
        return "N/A"
    try:
        if pd.isna(x):
            return "N/A"
    except Exception:
        pass
    return f"{float(x):.{digits}f}"


def _doc_market_text(row: pd.Series) -> str:
    """
    Build the narrative text chunk for one (metro, month) market data document.
    Contains Redfin housing metrics only — FRED macro data is queried at RAG time.
    Metric formatting is driven by the semantic model.
    """
    metro = row["metro_name"]
    state = row["state"]
    period_date = row["period_date"]

    lines = [f"Metro: {metro}, {state}. Month: {period_date}."]

    for mc in get_semantic_model().vector_metric_configs():
        value = _fmt(row.get(mc["key"]), mc["digits"])
        line = f"{mc['display_name']}: {mc['prefix']}{value}{mc['suffix']}"

        # Append MoM/YoY inline if defined
        extras = []
        if mc["mom_column"]:
            extras.append(f"MoM: {_fmt(row.get(mc['mom_column']), 2)}%")
        if mc["yoy_column"]:
            extras.append(f"YoY: {_fmt(row.get(mc['yoy_column']), 2)}%")
        if extras:
            line += f" ({', '.join(extras)})"

        lines.append(line + ".")

    return "\n".join(lines)


def _doc_trend_text(metro_name: str, state: str, metro_df: pd.DataFrame) -> str:
    """
    Build an 18-month trend summary document for a metro.
    Answers "how has X changed over time?" questions without needing point-in-time retrieval.
    Metric list and formatting driven by the semantic model.
    """
    df = metro_df.sort_values("period_date").copy()
    if len(df) < 2:
        return f"Metro: {metro_name}, {state}. Insufficient data for trend analysis."

    first = df.iloc[0]
    last = df.iloc[-1]
    date_start = first["period_date"]
    date_end = last["period_date"]
    n_months = len(df)

    def _trend_line(mc: dict) -> str:
        col = mc["key"]
        label = mc["display_name"]
        prefix = mc["prefix"]
        suffix = mc["suffix"]
        digits = mc["digits"]

        v0 = first.get(col)
        v1 = last.get(col)
        if v0 is None or v1 is None:
            try:
                if pd.isna(v0) or pd.isna(v1):
                    return f"{label}: N/A"
            except Exception:
                return f"{label}: N/A"
        v0, v1 = float(v0), float(v1)
        change = v1 - v0
        pct = (change / v0 * 100) if v0 != 0 else 0
        direction = "rising" if change > 0 else ("falling" if change < 0 else "flat")
        return (
            f"{label}: {prefix}{v0:,.{digits}f}{suffix} \u2192 {prefix}{v1:,.{digits}f}{suffix} "
            f"({change:+,.{digits}f}{suffix}, {pct:+.1f}%) \u2014 {direction}"
        )

    lines = [
        f"Metro: {metro_name}, {state}. 18-Month Trend Summary ({date_start} to {date_end}, {n_months} months).",
    ]

    for mc in get_semantic_model().vector_metric_configs():
        # Skip derived columns (mom/yoy) — they're not trended independently
        lines.append(_trend_line(mc))

        # For the primary price metric, add a min/max range line
        if mc["key"] == "median_sale_price" and "median_sale_price" in df.columns:
            price_min = df["median_sale_price"].min()
            price_max = df["median_sale_price"].max()
            price_min_str = f"${price_min:,.0f}" if price_min and not pd.isna(price_min) else "N/A"
            price_max_str = f"${price_max:,.0f}" if price_max and not pd.isna(price_max) else "N/A"
            lines.append(f"  Price range over period: {price_min_str} (low) to {price_max_str} (high)")

    return "\n".join(lines)


def _doc_metric_definitions_text() -> str:
    return get_semantic_model().grounding_text()


def _read_redfin_table(cfg: Config) -> pd.DataFrame:
    _print_header("Reading SQLite tables")

    if not os.path.exists(cfg.db_path):
        raise FileNotFoundError(f"SQLite DB not found: {cfg.db_path}")

    with sqlite3.connect(cfg.db_path) as conn:
        redfin = pd.read_sql_query(f"SELECT * FROM {cfg.redfin_table}", conn)

    print(f"Redfin rows: {len(redfin):,}")

    required_redfin = [
        "period_date",
        "metro_name",
        "state",
        "median_sale_price",
        "days_on_market",
        "inventory",
        "price_drop_pct",
    ]
    missing_redfin = [c for c in required_redfin if c not in redfin.columns]
    if missing_redfin:
        raise RuntimeError(f"Missing columns in {cfg.redfin_table}: {missing_redfin}")

    return redfin


def _build_documents(
    cfg: Config, redfin_df: pd.DataFrame
) -> Tuple[List[str], List[Dict[str, str]], List[str]]:
    _print_header("Building Chroma documents")

    redfin = redfin_df.copy()
    redfin["period_date_dt"] = pd.to_datetime(redfin["period_date"], errors="coerce")
    redfin = redfin.dropna(subset=["period_date_dt"]).copy()

    documents: List[str] = []
    metadatas: List[Dict[str, str]] = []
    ids: List[str] = []

    date_min = redfin["period_date"].min()
    date_max = redfin["period_date"].max()

    for i, row in enumerate(redfin.itertuples(index=False), start=1):
        rdict = row._asdict()
        metro_name = str(rdict["metro_name"])
        state = str(rdict["state"])
        period_date = str(rdict["period_date"])

        doc_text = _doc_market_text(pd.Series(rdict))
        doc_id = _safe_id(f"market_data::{state}::{metro_name}::{period_date}")

        documents.append(doc_text)
        metadatas.append(
            {
                "metro_name": metro_name,
                "state": state,
                "period_date": period_date,
                "doc_type": "market_data",
            }
        )
        ids.append(doc_id)

        if i % cfg.log_every == 0:
            print(f"Market docs built: {i:,} (range so far: {date_min} to {date_max})")

    print(f"Total market data docs: {len(documents):,}")

    # Per-metro trend documents (one per metro, summarizing the full date range)
    _print_header("Building per-metro trend documents")
    trend_count = 0
    for metro_name, group in redfin.groupby("metro_name"):
        state = group["state"].iloc[0] if not group.empty else "N/A"
        trend_text = _doc_trend_text(str(metro_name), str(state), group)
        trend_id = _safe_id(f"metro_trend::{state}::{metro_name}")

        documents.append(trend_text)
        metadatas.append(
            {
                "metro_name": str(metro_name),
                "state": str(state),
                "period_date": "ALL",
                "doc_type": "metro_trend",
            }
        )
        ids.append(trend_id)
        trend_count += 1

    print(f"Trend docs built: {trend_count:,}")

    # Metric definitions doc: exactly one
    documents.append(_doc_metric_definitions_text())
    metadatas.append(
        {
            "metro_name": "ALL",
            "state": "ALL",
            "period_date": "ALL",
            "doc_type": "metric_definition",
        }
    )
    ids.append("metric_definition::v1")

    return documents, metadatas, ids


def _clear_and_create_collection(
    cfg: Config, embedding_function: SentenceTransformerEmbeddingFunction
) -> chromadb.Collection:
    _print_header("Clearing/rebuilding Chroma collection")

    _ensure_parent_dir(cfg.chroma_dir)

    client = chromadb.PersistentClient(path=cfg.chroma_dir)

    # Clear collection if it exists (idempotent rebuild)
    try:
        client.get_collection(name=cfg.collection_name)
        client.delete_collection(name=cfg.collection_name)
        print(f"Deleted existing collection: {cfg.collection_name}")
    except Exception:
        pass

    collection = client.create_collection(
        name=cfg.collection_name,
        embedding_function=embedding_function,
    )
    return collection


def main() -> None:
    load_dotenv()  # requirement: use python-dotenv
    cfg = Config()

    try:
        print("\nStarting vector store rebuild...")

        redfin_df = _read_redfin_table(cfg)

        # Dedup to prevent Chroma DuplicateIDError when building doc_ids.
        # Keep one row per (metro_name, state, period_date).
        before = len(redfin_df)
        redfin_df = redfin_df.drop_duplicates(subset=["metro_name", "state", "period_date"])
        after = len(redfin_df)
        if after != before:
            print(f"Deduplicated Redfin rows: {before:,} -> {after:,}")

        embedding_function = SentenceTransformerEmbeddingFunction(
            cfg.embedding_model_name,
            cfg.embedding_cache_dir,
        )

        # Recreate collection from scratch
        collection = _clear_and_create_collection(cfg, embedding_function)

        documents, metadatas, ids = _build_documents(cfg, redfin_df)

        _print_header("Embedding + adding to Chroma")

        # Batch add to keep memory stable
        batch_size = cfg.chroma_add_batch_size
        total = len(documents)

        start_ts = datetime.now(timezone.utc)
        for start in range(0, total, batch_size):
            end = min(start + batch_size, total)
            batch_docs = documents[start:end]
            batch_metas = metadatas[start:end]
            batch_ids = ids[start:end]

            collection.add(documents=batch_docs, metadatas=batch_metas, ids=batch_ids)

            print(f"Added {end:,}/{total:,} documents...")

        elapsed_s = (datetime.now(timezone.utc) - start_ts).total_seconds()

        # Summary
        total_docs = len(documents)
        market_metas = [m for m in metadatas if m["doc_type"] == "market_data"]
        trend_metas = [m for m in metadatas if m["doc_type"] == "metro_trend"]
        unique_metros = {(m["metro_name"], m["state"]) for m in market_metas}
        period_dates = [m["period_date"] for m in market_metas]
        date_range = (min(period_dates), max(period_dates)) if period_dates else ("N/A", "N/A")

        _print_header("Summary")
        print(f"Total documents embedded: {total_docs:,}")
        print(f"  Market data docs: {len(market_metas):,}")
        print(f"  Trend summary docs: {len(trend_metas):,}")
        print(f"  Other (metric defs etc): {total_docs - len(market_metas) - len(trend_metas):,}")
        print(f"Metros covered: {len(unique_metros):,}")
        print(f"Date range: {date_range[0]} to {date_range[1]}")
        print(f"Elapsed: {elapsed_s:.1f}s")

        print("\nVector store rebuild complete successfully.")

    except Exception as e:
        print(f"FAIL: Unexpected error while rebuilding vectors: {e}")
        raise


if __name__ == "__main__":
    main()