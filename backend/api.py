"""
HomeSignal Backend API.

This module is the single point of contact between the frontend and all backend
services (SQLite, RAG engine, ChatEngine, pipeline refresh).  The frontend
should NEVER import sqlite3, backend.rag, or backend.chat_engine directly —
everything goes through functions defined here.

All functions are plain Python (no Streamlit dependency) so any frontend
(Streamlit, Flask, CLI) can consume them.
"""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
import sqlite3
import threading
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Callable, Dict, List, Optional, Tuple

import pandas as pd

from backend.chat_engine import ChatEngine
from backend.cloud_sync import sync_cloud_snapshot_if_needed
from backend.rag import Config, RAGEngine
from backend.semantic_model import get_semantic_model

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

DB_PATH = "data/homesignal.db"

# ---------------------------------------------------------------------------
# Internal singleton management (lazy init, thread-safe)
# ---------------------------------------------------------------------------

_lock = threading.Lock()
_rag_engine: Optional[RAGEngine] = None
_rag_tooltip_engine: Optional[RAGEngine] = None
_chat_engine: Optional[ChatEngine] = None


def _get_rag() -> RAGEngine:
    global _rag_engine
    if _rag_engine is None:
        with _lock:
            if _rag_engine is None:
                _rag_engine = RAGEngine()
    return _rag_engine


def _get_rag_tooltip() -> RAGEngine:
    global _rag_tooltip_engine
    if _rag_tooltip_engine is None:
        with _lock:
            if _rag_tooltip_engine is None:
                _rag_tooltip_engine = RAGEngine(
                    cfg=Config(claude_model="claude-haiku-4-5-20251001", claude_max_tokens=60)
                )
    return _rag_tooltip_engine


def _get_chat_engine() -> ChatEngine:
    global _chat_engine
    if _chat_engine is None:
        with _lock:
            if _chat_engine is None:
                _chat_engine = ChatEngine()
    return _chat_engine


def reset_engines() -> None:
    """Force re-creation of engine singletons (e.g. after a data refresh)."""
    global _rag_engine, _rag_tooltip_engine, _chat_engine
    with _lock:
        _rag_engine = None
        _rag_tooltip_engine = None
        _chat_engine = None


def maybe_sync_cloud_data(force: bool = False) -> Dict[str, Any]:
    """
    Pull latest runtime snapshot from cloud storage if configured.

    If a newer snapshot is applied, reset engine singletons so future calls
    reopen SQLite/Chroma against the new on-disk data.
    """
    result = sync_cloud_snapshot_if_needed(force=force)
    if result.get("updated"):
        reset_engines()
    return result


# ---------------------------------------------------------------------------
# Low-level DB helpers
# ---------------------------------------------------------------------------

def _db_read_df(query: str, params: Tuple[Any, ...] = ()) -> pd.DataFrame:
    with sqlite3.connect(DB_PATH) as conn:
        return pd.read_sql_query(query, conn, params=params)


def _db_read_scalar(query: str, params: Tuple[Any, ...] = ()) -> Any:
    with sqlite3.connect(DB_PATH) as conn:
        cur = conn.cursor()
        cur.execute(query, params)
        row = cur.fetchone()
        return None if row is None else row[0]


# ---------------------------------------------------------------------------
# MoM helper (used by metric computation)
# ---------------------------------------------------------------------------

def _is_na(x: Any) -> bool:
    """Check if a value is None or NaN."""
    if x is None:
        return True
    try:
        import math
        return isinstance(x, float) and math.isnan(x)
    except (TypeError, ValueError):
        return False


def mom_percent(cur_val: Any, prev_val: Any) -> Optional[float]:
    """Compute month-over-month percent change: (cur - prev) / prev * 100."""
    if _is_na(cur_val):
        return None
    if _is_na(prev_val):
        return None
    try:
        prev = float(prev_val)
        cur = float(cur_val)
        if prev == 0:
            return None
        return (cur - prev) / prev * 100.0
    except Exception:
        return None


# ---------------------------------------------------------------------------
# Table management
# ---------------------------------------------------------------------------

def ensure_ai_tables() -> None:
    """Create AI caching tables if they do not already exist."""
    with sqlite3.connect(DB_PATH) as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS ai_tooltips (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                metro_name TEXT NOT NULL,
                state TEXT,
                period_date TEXT NOT NULL,
                metric_key TEXT NOT NULL,
                tooltip_text TEXT NOT NULL,
                generated_at TEXT NOT NULL,
                UNIQUE(metro_name, state, period_date, metric_key)
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS ai_briefs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                metro_name TEXT NOT NULL,
                state TEXT,
                brief_date TEXT NOT NULL,
                brief_text TEXT NOT NULL,
                generated_at TEXT NOT NULL,
                UNIQUE(metro_name, state, brief_date)
            )
            """
        )
        conn.commit()


# ---------------------------------------------------------------------------
# Data reading APIs
# ---------------------------------------------------------------------------

def get_metros() -> pd.DataFrame:
    """Return distinct metros ordered by name."""
    return _db_read_df(
        """
        SELECT DISTINCT metro_name, state
        FROM redfin_metrics
        WHERE region_type = 'metro'
        ORDER BY metro_name
        """
    )


def get_last_updated_date() -> Optional[str]:
    max_pd = _db_read_scalar("SELECT MAX(period_date) FROM redfin_metrics")
    return str(max_pd) if max_pd else None


def get_data_freshness() -> Dict[str, Dict[str, Optional[str]]]:
    """Return freshness info for both data sources."""
    result: Dict[str, Dict[str, Optional[str]]] = {"fred": {}, "redfin": {}}
    try:
        result["fred"] = {
            "latest_period": _db_read_scalar(
                "SELECT MAX(period_date) FROM fred_metrics WHERE series_id = 'MORTGAGE30US'"
            ),
            "loaded_at": _db_read_scalar("SELECT MAX(loaded_at) FROM fred_metrics"),
        }
    except Exception:
        result["fred"] = {"latest_period": None, "loaded_at": None}
    try:
        result["redfin"] = {
            "latest_period": _db_read_scalar("SELECT MAX(period_date) FROM redfin_metrics"),
            "loaded_at": _db_read_scalar("SELECT MAX(loaded_at) FROM redfin_metrics"),
        }
    except Exception:
        result["redfin"] = {"latest_period": None, "loaded_at": None}
    return result


def get_dashboard_card_config() -> List[Dict[str, Any]]:
    """Return ordered dashboard card definitions from the semantic model."""
    return get_semantic_model().dashboard_cards()


def get_latest_metrics_for_metro(metro_name: str) -> Dict[str, Any]:
    """Return latest-month snapshot with computed MoM deltas."""
    df = _db_read_df(
        """
        SELECT
            period_date, metro_name, state,
            median_sale_price, days_on_market, inventory,
            price_drop_pct, homes_sold, new_listings,
            months_of_supply, price_mom, inventory_mom
        FROM redfin_metrics
        WHERE metro_name = ?
          AND region_type = 'metro'
        ORDER BY period_date ASC
        """,
        (metro_name,),
    )
    if df.empty:
        return {}

    group_cols = ["period_date", "metro_name", "state"]
    agg_df = (
        df.groupby(group_cols, as_index=False)
        .agg(
            median_sale_price=("median_sale_price", "mean"),
            days_on_market=("days_on_market", "mean"),
            inventory=("inventory", "mean"),
            price_drop_pct=("price_drop_pct", "mean"),
            homes_sold=("homes_sold", "sum"),
            new_listings=("new_listings", "sum"),
            months_of_supply=("months_of_supply", "mean"),
            price_mom=("price_mom", "mean"),
            inventory_mom=("inventory_mom", "mean"),
        )
        .sort_values("period_date")
    )

    latest = agg_df.iloc[-1].to_dict()
    prev = agg_df.iloc[-2].to_dict() if len(agg_df) >= 2 else None

    return {
        "state": latest.get("state"),
        "period_date": latest.get("period_date"),
        "median_sale_price": latest.get("median_sale_price"),
        "median_sale_price_mom_pct": mom_percent(
            latest.get("median_sale_price"), (prev or {}).get("median_sale_price")
        ),
        "days_on_market": latest.get("days_on_market"),
        "days_on_market_mom_pct": mom_percent(
            latest.get("days_on_market"), (prev or {}).get("days_on_market")
        ),
        "inventory": latest.get("inventory"),
        "inventory_mom_pct": mom_percent(
            latest.get("inventory"), (prev or {}).get("inventory")
        )
        if prev is not None
        else latest.get("inventory_mom"),
        "price_drop_pct": latest.get("price_drop_pct"),
        "price_drop_pct_mom_pct": mom_percent(
            latest.get("price_drop_pct"), (prev or {}).get("price_drop_pct")
        ),
    }


def get_latest_mortgage_rate_with_mom() -> Dict[str, Any]:
    """Return latest mortgage rate with approximate MoM based on monthly averages."""
    fred = _db_read_df(
        """
        SELECT period_date, value
        FROM fred_metrics
        WHERE series_id='MORTGAGE30US'
        ORDER BY period_date ASC
        """
    )
    if fred.empty:
        return {}

    fred["period_date_dt"] = pd.to_datetime(fred["period_date"], errors="coerce")
    fred = fred.dropna(subset=["period_date_dt"]).copy()
    fred["month_key"] = fred["period_date_dt"].dt.strftime("%Y-%m")

    monthly = (
        fred.groupby("month_key", as_index=False)["value"]
        .mean()
        .rename(columns={"value": "mortgage_rate_avg"})
        .sort_values("month_key")
    )
    if monthly.empty:
        return {}

    latest_month = monthly.iloc[-1].to_dict()
    prev_month = monthly.iloc[-2].to_dict() if len(monthly) >= 2 else None

    latest_rate = float(latest_month["mortgage_rate_avg"])
    mom_pct = None
    if prev_month is not None:
        mom_pct = mom_percent(latest_rate, float(prev_month["mortgage_rate_avg"]))

    latest_week_row = fred.sort_values("period_date_dt").iloc[-1]
    return {
        "latest_rate": latest_rate,
        "latest_period_date": str(latest_week_row["period_date"]),
        "mom_pct": mom_pct,
    }


def get_trend_series(metro_name: str, months: int = 12) -> pd.DataFrame:
    """Return last N months of median_sale_price for the metro."""
    df = _db_read_df(
        """
        SELECT period_date, median_sale_price
        FROM redfin_metrics
        WHERE metro_name = ?
          AND region_type = 'metro'
        ORDER BY period_date ASC
        """,
        (metro_name,),
    )
    if df.empty:
        return df

    df = df.groupby("period_date", as_index=False)["median_sale_price"].mean()
    df["period_date_dt"] = pd.to_datetime(df["period_date"], errors="coerce")
    df = df.dropna(subset=["period_date_dt"]).sort_values("period_date_dt")
    if len(df) > months:
        df = df.iloc[-months:]
    return df


def get_feedback_stats() -> pd.DataFrame:
    """Return feedback counts grouped by metro."""
    return _db_read_df(
        """
        SELECT
            COALESCE(metro, 'All metros') AS metro,
            SUM(CASE WHEN feedback='up' THEN 1 ELSE 0 END) AS thumbs_up,
            SUM(CASE WHEN feedback='down' THEN 1 ELSE 0 END) AS thumbs_down,
            COUNT(*) AS total
        FROM feedback
        GROUP BY metro
        ORDER BY total DESC
        """
    )


# ---------------------------------------------------------------------------
# Tooltip APIs
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class TooltipCacheRow:
    tooltip_text: str
    generated_at: str


def get_tooltip_cache(
    metro_name: str, state: Optional[str], period_date: str, metric_key: str
) -> Optional[TooltipCacheRow]:
    row = _db_read_df(
        """
        SELECT tooltip_text, generated_at
        FROM ai_tooltips
        WHERE metro_name=? AND (state=? OR (state IS NULL AND ? IS NULL))
          AND period_date=? AND metric_key=?
        LIMIT 1
        """,
        (metro_name, state, state, period_date, metric_key),
    )
    if row.empty:
        return None
    return TooltipCacheRow(tooltip_text=row.iloc[0]["tooltip_text"], generated_at=row.iloc[0]["generated_at"])


def _upsert_tooltip_cache(
    metro_name: str,
    state: Optional[str],
    period_date: str,
    metric_key: str,
    tooltip_text: str,
    generated_at: str,
) -> None:
    with sqlite3.connect(DB_PATH) as conn:
        conn.execute(
            """
            INSERT OR REPLACE INTO ai_tooltips
            (metro_name, state, period_date, metric_key, tooltip_text, generated_at)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (metro_name, state, period_date, metric_key, tooltip_text, generated_at),
        )
        conn.commit()


def _generate_tooltip_insight(metro_name: str, period_date: str) -> str:
    """Generate a one-sentence tooltip insight via RAG (Haiku)."""
    from backend.formatting_utils import answer_with_superscript_citations

    rag = _get_rag_tooltip()
    question = (
        f"What are the key housing market trends "
        f"in {metro_name} as of {period_date}? "
        f"Answer in one short sentence."
    )
    res = rag.query(question, metro_filter=metro_name)
    answer = res["answer"]
    if res.get("confidence") == "low" or "don't have enough data" in (answer or "").lower():
        try:
            res = rag.query(question, metro_filter=None)
            answer = res["answer"]
        except Exception:
            pass
    return answer_with_superscript_citations(answer or "").strip()


def get_or_create_tooltips(
    metro_name: str,
    state: Optional[str],
    trend_df: pd.DataFrame,
    on_progress: Optional[Callable[[int, int], None]] = None,
) -> Tuple[List[str], List[str]]:
    """
    Ensure tooltips exist for every data point in trend_df.

    Args:
        on_progress: Optional callback(completed, total) for UI progress updates.

    Returns:
        (tooltip_texts, generated_ats)
    """
    from backend.formatting_utils import truncate_tooltip_text

    metric_key = "median_sale_price"
    tooltip_texts: List[str] = []
    generated_ats: List[str] = []

    total = len(trend_df)
    completed = 0
    ordered_periods: List[str] = []
    result_by_period: Dict[str, Tuple[str, str]] = {}
    missing_periods: List[str] = []

    for _, row in trend_df.iterrows():
        period_date = str(row["period_date"])
        ordered_periods.append(period_date)

        cached = get_tooltip_cache(metro_name, state, period_date, metric_key)
        if cached:
            result_by_period[period_date] = (
                truncate_tooltip_text(cached.tooltip_text),
                cached.generated_at,
            )
            completed += 1
            if on_progress:
                on_progress(completed, total)
            continue

        missing_periods.append(period_date)

    if missing_periods:
        max_workers = min(4, len(missing_periods))
        with ThreadPoolExecutor(max_workers=max_workers) as ex:
            future_to_period = {
                ex.submit(_generate_tooltip_insight, metro_name, period): period
                for period in missing_periods
            }
            for fut in as_completed(future_to_period):
                period_date = future_to_period[fut]
                tooltip_text = truncate_tooltip_text(fut.result(), max_chars=150)
                generated_at = datetime.now(timezone.utc).isoformat(timespec="seconds")

                # Keep DB writes serialized to avoid SQLite lock contention.
                _upsert_tooltip_cache(
                    metro_name=metro_name,
                    state=state,
                    period_date=period_date,
                    metric_key=metric_key,
                    tooltip_text=tooltip_text,
                    generated_at=generated_at,
                )
                result_by_period[period_date] = (tooltip_text, generated_at)
                completed += 1
                if on_progress:
                    on_progress(completed, total)

    for period_date in ordered_periods:
        tooltip_text, generated_at = result_by_period[period_date]
        tooltip_texts.append(tooltip_text)
        generated_ats.append(generated_at)

    return tooltip_texts, generated_ats


# ---------------------------------------------------------------------------
# Daily Brief API
# ---------------------------------------------------------------------------

def _get_brief_cache(
    metro_name: str, state: Optional[str], brief_date: str
) -> Optional[Tuple[str, str]]:
    row = _db_read_df(
        """
        SELECT brief_text, generated_at
        FROM ai_briefs
        WHERE metro_name=? AND (state=? OR (state IS NULL AND ? IS NULL))
          AND brief_date=?
        LIMIT 1
        """,
        (metro_name, state, state, brief_date),
    )
    if row.empty:
        return None
    return row.iloc[0]["brief_text"], row.iloc[0]["generated_at"]


def _upsert_brief_cache(
    metro_name: str,
    state: Optional[str],
    brief_date: str,
    brief_text: str,
    generated_at: str,
) -> None:
    with sqlite3.connect(DB_PATH) as conn:
        conn.execute(
            """
            INSERT OR REPLACE INTO ai_briefs
            (metro_name, state, brief_date, brief_text, generated_at)
            VALUES (?, ?, ?, ?, ?)
            """,
            (metro_name, state, brief_date, brief_text, generated_at),
        )
        conn.commit()


def get_or_create_daily_brief(metro_name: str, state: Optional[str]) -> Dict[str, Any]:
    """Return today's AI brief for the metro, generating if not cached."""
    brief_date = datetime.now(timezone.utc).date().isoformat()
    cached = _get_brief_cache(metro_name, state, brief_date)
    if cached:
        brief_text, generated_at = cached
        return {
            "brief_text": brief_text,
            "generated_at": generated_at,
            "brief_date": brief_date,
            "from_cache": True,
        }

    rag = _get_rag()
    question = (
        f"Write a daily HomeSignal market brief for {metro_name} ({state or 'state unknown'}) "
        f"as of {brief_date}. Focus on the key takeaways from: median sale price, days on market, "
        f"inventory, price drop percentage, and the 30yr mortgage rate. Keep it concise (2-3 sentences)."
    )
    res = rag.query(question, metro_filter=metro_name)
    brief_text = res["answer"]
    generated_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
    _upsert_brief_cache(metro_name, state, brief_date, brief_text, generated_at)
    return {
        "brief_text": brief_text,
        "generated_at": generated_at,
        "brief_date": brief_date,
        "from_cache": False,
    }


# ---------------------------------------------------------------------------
# Chat API
# ---------------------------------------------------------------------------

def chat(
    question: str,
    conversation_history: Optional[List[Dict[str, str]]] = None,
    metro_filter: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Send a chat question through the hybrid RAG + SQL engine.

    Returns:
        {
            "answer": str,
            "sources": list[str],
            "retrieved_docs": list[str],
            "confidence": "high" | "medium" | "low",
            "detected_metros": list[str],
        }
    """
    engine = _get_chat_engine()
    return engine.chat(
        question,
        conversation_history=conversation_history,
        metro_filter=metro_filter,
    )


def log_feedback(
    question: str, answer: str, feedback: str, metro: Optional[str] = None
) -> None:
    """Log thumbs up/down feedback."""
    engine = _get_chat_engine()
    engine.log_feedback(question, answer, feedback, metro)


