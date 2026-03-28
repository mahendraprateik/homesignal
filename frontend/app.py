"""
HomeSignal Streamlit app.

Features:
- Metro selector (from SQLite)
- 5 metric cards with MoM deltas
- 12-month trend chart for median sale price with AI-generated hover tooltips
  cached in SQLite table `ai_tooltips`
- Daily AI market brief cached in SQLite table `ai_briefs`
- RAG-powered chat with thumbs up/down feedback cached via RAGEngine

Run:
  ./venv/bin/streamlit run frontend/app.py
"""

from __future__ import annotations

import sys
import os
import re
import html

# Ensure the project root is on `sys.path` so `backend/` is importable when
# running `streamlit run frontend/app.py` from the repo root.
sys.path.insert(
    0,
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
)

import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

import pandas as pd
import plotly.graph_objects as go
import streamlit as st

from backend.rag import Config, RAGEngine


DB_PATH = "data/homesignal.db"


@dataclass(frozen=True)
class TooltipCacheRow:
    tooltip_text: str
    generated_at: str


def _ensure_ai_tables() -> None:
    """
    Creates AI caching tables if they do not already exist.
    """
    with sqlite3.connect(DB_PATH) as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS ai_tooltips (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                metro_name TEXT NOT NULL,
                state TEXT,
                period_date TEXT NOT NULL,  -- YYYY-MM-DD (month start)
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
                brief_date TEXT NOT NULL,  -- YYYY-MM-DD
                brief_text TEXT NOT NULL,
                generated_at TEXT NOT NULL,
                UNIQUE(metro_name, state, brief_date)
            )
            """
        )
        conn.commit()


def _db_read_df(query: str, params: Tuple[Any, ...] = ()) -> pd.DataFrame:
    with sqlite3.connect(DB_PATH) as conn:
        return pd.read_sql_query(query, conn, params=params)


def _db_read_scalar(query: str, params: Tuple[Any, ...] = ()) -> Any:
    with sqlite3.connect(DB_PATH) as conn:
        cur = conn.cursor()
        cur.execute(query, params)
        row = cur.fetchone()
        return None if row is None else row[0]


def _format_money(x: Any) -> str:
    if x is None or pd.isna(x):
        return "N/A"
    try:
        v = float(x)
        return f"${v:,.0f}"
    except Exception:
        return "N/A"


def _format_number(x: Any) -> str:
    if x is None or pd.isna(x):
        return "N/A"
    try:
        return f"{float(x):,.0f}"
    except Exception:
        return "N/A"


def _format_pct(x: Any) -> str:
    if x is None or pd.isna(x):
        return "N/A"
    try:
        return f"{float(x):.2f}%"
    except Exception:
        return "N/A"


def _normalize_price_drop_pct_for_display(x: Any) -> Optional[float]:
    """
    Redfin's PRICE_DROPS can be stored either as:
    - percentage in 0-100 range (e.g., 29.0)
    - decimal in 0-1 range (e.g., 0.29)

    For UI display, ensure we return a 0-100 percentage value.
    """
    if x is None or pd.isna(x):
        return None
    try:
        v = float(x)
        if v < 1.0:
            v = v * 100.0
        return v
    except Exception:
        return None


def _answer_with_superscript_citations(answer: str) -> str:
    """
    Removes ALL citation markers from an answer text.

    This strips:
    - bracket citations like [1], [2], clusters like [1][2][3]
    - superscript citations like <sup>1</sup>
    - any trailing "Sources: ..." fallback appended by the RAG engine
    """
    if not answer:
        return ""
    # If the RAG engine added a fallback "Sources:" line, remove it from the
    # main answer since the UI already shows sources in an expander.
    answer = re.sub(r"(?im)^\s*Sources:\s*.*$", "", answer).strip()

    # Remove superscript citations.
    answer = re.sub(r"<sup>\s*\d+\s*</sup>", "", answer, flags=re.IGNORECASE)

    # Remove bracket citations (including clustered styles like [1][2]).
    answer = re.sub(r"\[\s*\d+\s*\]", "", answer)

    # Clean up whitespace after removals.
    answer = re.sub(r"\s{2,}", " ", answer).strip()
    return answer


def _render_chat_answer_preserving_dollars(text: str) -> str:
    """
    Renders an answer via HTML in `st.markdown` while preserving dollar signs
    and preventing markdown/LaTeX parsing from interfering with "$...".
    """
    cleaned = _answer_with_superscript_citations(text)
    escaped = html.escape(cleaned)
    # Avoid MathJax/LaTeX triggering in Markdown by escaping $.
    escaped = escaped.replace("$", "&#36;")
    return f"<div style='white-space: pre-wrap;'>{escaped}</div>"


def _truncate_tooltip_text(text: str, max_chars: int = 150) -> str:
    """
    Truncates tooltip text to `max_chars` and appends "..." when truncated.
    """
    if text is None:
        return ""
    s = str(text).strip()
    if len(s) <= max_chars:
        return s
    # Reserve space for "..."
    if max_chars <= 3:
        return s[:max_chars]
    return s[: max_chars - 3].rstrip() + "..."



def _mom_percent(cur_val: Any, prev_val: Any) -> Optional[float]:
    """
    Computes MoM percent change: (cur - prev) / prev * 100.
    Returns None if prev is missing or 0.
    """
    if cur_val is None or pd.isna(cur_val):
        return None
    if prev_val is None or pd.isna(prev_val):
        return None
    try:
        prev = float(prev_val)
        cur = float(cur_val)
        if prev == 0:
            return None
        return (cur - prev) / prev * 100.0
    except Exception:
        return None



@st.cache_resource(show_spinner=False)
def get_rag() -> RAGEngine:
    return RAGEngine()


@st.cache_resource(show_spinner=False)
def get_rag_tooltip() -> RAGEngine:
    # Haiku for cost-efficient one-sentence hover tooltips.
    return RAGEngine(cfg=Config(claude_model="claude-haiku-4-5-20251001", claude_max_tokens=60))


@st.cache_data(ttl=3600)
def get_metros() -> pd.DataFrame:
    """
    Returns distinct metros with a stable ordering for the dropdown.
    """
    df = _db_read_df(
        """
        SELECT DISTINCT metro_name, state
        FROM redfin_metrics
        ORDER BY metro_name
        """
    )
    return df


@st.cache_data(ttl=3600)
def get_last_updated_date() -> Optional[str]:
    max_pd = _db_read_scalar("SELECT MAX(period_date) FROM redfin_metrics")
    if not max_pd:
        return None
    return str(max_pd)


@st.cache_data(ttl=3600)
def get_latest_metrics_for_metro(metro_name: str) -> Dict[str, Any]:
    """
    Returns latest month snapshot (aggregating duplicates if any).
    """
    df = _db_read_df(
        """
        SELECT
            period_date,
            metro_name,
            state,
            median_sale_price,
            days_on_market,
            inventory,
            price_drop_pct,
            homes_sold,
            new_listings,
            months_of_supply,
            price_mom,
            inventory_mom
        FROM redfin_metrics
        WHERE metro_name = ?
        ORDER BY period_date ASC
        """,
        (metro_name,),
    )
    if df.empty:
        return {}

    # Aggregate in case ingest produced duplicates for (metro, month).
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

    # Compute MoM deltas where we may not have explicit *_mom columns.
    return {
        "state": latest.get("state"),
        "period_date": latest.get("period_date"),
        "median_sale_price": latest.get("median_sale_price"),
        "median_sale_price_mom_pct": _mom_percent(
            latest.get("median_sale_price"), (prev or {}).get("median_sale_price")
        ),
        "days_on_market": latest.get("days_on_market"),
        "days_on_market_mom_pct": _mom_percent(
            latest.get("days_on_market"), (prev or {}).get("days_on_market")
        ),
        "inventory": latest.get("inventory"),
        "inventory_mom_pct": _mom_percent(
            latest.get("inventory"), (prev or {}).get("inventory")
        )
        if prev is not None
        else latest.get("inventory_mom"),
        "price_drop_pct": latest.get("price_drop_pct"),
        "price_drop_pct_mom_pct": _mom_percent(
            latest.get("price_drop_pct"), (prev or {}).get("price_drop_pct")
        ),
        # Not computed here; computed separately from fred_metrics
    }


@st.cache_data(ttl=3600)
def get_latest_mortgage_rate_with_mom() -> Dict[str, Any]:
    """
    Uses the latest value and an approximate MoM based on the previous month average.
    """
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
        mom_pct = _mom_percent(latest_rate, float(prev_month["mortgage_rate_avg"]))

    # Latest weekly date for display purposes.
    latest_week_row = fred.sort_values("period_date_dt").iloc[-1]
    return {
        "latest_rate": latest_rate,
        "latest_period_date": str(latest_week_row["period_date"]),
        "mom_pct": mom_pct,
    }


@st.cache_data(ttl=3600)
def get_trend_series(metro_name: str, months: int = 12) -> pd.DataFrame:
    """
    Returns last N months of median_sale_price for the selected metro.
    Aggregates duplicates by month.
    """
    df = _db_read_df(
        """
        SELECT period_date, median_sale_price
        FROM redfin_metrics
        WHERE metro_name = ?
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


def get_tooltip_cache(
    metro_name: str, state: Optional[str], period_date: str, metric_key: str
) -> Optional[TooltipCacheRow]:
    with sqlite3.connect(DB_PATH) as conn:
        cur = conn.cursor()
        cur.execute(
            """
            SELECT tooltip_text, generated_at
            FROM ai_tooltips
            WHERE metro_name=? AND (state=? OR (state IS NULL AND ? IS NULL))
              AND period_date=? AND metric_key=?
            LIMIT 1
            """,
            (metro_name, state, state, period_date, metric_key),
        )
        row = cur.fetchone()
        if not row:
            return None
        return TooltipCacheRow(tooltip_text=row[0], generated_at=row[1])


def upsert_tooltip_cache(
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


def generate_tooltip_insight(
    rag: RAGEngine,
    metro_name: str,
    period_date: str,
) -> str:
    """
    Generates a one-sentence tooltip insight for a specific data point.
    Cached by (metro_name, period_date, metric_key) in ai_tooltips.
    """
    # Tooltip prompt intentionally avoids over-specific temporal phrasing
    # so retrieval has enough signal to find the metro's relevant docs.
    question = (
        f"What are the key housing market trends "
        f"in {metro_name} as of {period_date}? "
        f"Answer in one short sentence."
    )
    res = rag.query(question, metro_filter=metro_name)
    answer = res["answer"]
    # If metro_filter string does not match Chroma metadata, retry unfiltered.
    if res.get("confidence") == "low" or "don't have enough data" in (answer or "").lower():
        try:
            res = rag.query(question, metro_filter=None)
            answer = res["answer"]
        except Exception:
            pass

    return _answer_with_superscript_citations(answer or "").strip()


def get_or_create_tooltips(
    metro_name: str, state: Optional[str], trend_df: pd.DataFrame,
    progress_bar=None,
) -> Tuple[List[str], List[str]]:
    """
    Ensures tooltips exist for each point and returns (tooltip_texts, generated_ats).
    """
    tooltip_texts: List[str] = []
    generated_ats: List[str] = []

    # Metric key for caching.
    metric_key = "median_sale_price"

    rag_tooltip = get_rag_tooltip()

    for _, row in trend_df.iterrows():
        period_date = str(row["period_date"])

        cached = get_tooltip_cache(metro_name, state, period_date, metric_key)
        if cached:
            tooltip_texts.append(_truncate_tooltip_text(cached.tooltip_text))
            generated_ats.append(cached.generated_at)
            continue

        tooltip_text = generate_tooltip_insight(
            rag=rag_tooltip,
            metro_name=metro_name,
            period_date=period_date,
        )
        if progress_bar is not None:
            progress_bar.progress(
                min(1.0, (len(tooltip_texts) + 1) / max(len(trend_df), 1)),
                text=f"Generating AI insights… {len(tooltip_texts) + 1}/{len(trend_df)}",
            )

        tooltip_text = _truncate_tooltip_text(tooltip_text, max_chars=150)
        generated_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
        upsert_tooltip_cache(
            metro_name=metro_name,
            state=state,
            period_date=period_date,
            metric_key=metric_key,
            tooltip_text=tooltip_text,
            generated_at=generated_at,
        )

        tooltip_texts.append(tooltip_text)
        generated_ats.append(generated_at)

    return tooltip_texts, generated_ats


def get_brief_cache(
    metro_name: str, state: Optional[str], brief_date: str
) -> Optional[Tuple[str, str]]:
    with sqlite3.connect(DB_PATH) as conn:
        cur = conn.cursor()
        cur.execute(
            """
            SELECT brief_text, generated_at
            FROM ai_briefs
            WHERE metro_name=? AND (state=? OR (state IS NULL AND ? IS NULL))
              AND brief_date=?
            LIMIT 1
            """,
            (metro_name, state, state, brief_date),
        )
        row = cur.fetchone()
        if not row:
            return None
        return row[0], row[1]


def upsert_brief_cache(
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


def get_or_create_daily_brief(rag: RAGEngine, metro_name: str, state: Optional[str]) -> Dict[str, Any]:
    brief_date = datetime.now(timezone.utc).date().isoformat()
    cached = get_brief_cache(metro_name, state, brief_date)
    if cached:
        brief_text, generated_at = cached
        return {"brief_text": brief_text, "generated_at": generated_at, "brief_date": brief_date, "from_cache": True}

    question = (
        f"Write a daily HomeSignal market brief for {metro_name} ({state or 'state unknown'}) "
        f"as of {brief_date}. Focus on the key takeaways from: median sale price, days on market, "
        f"inventory, price drop percentage, and the 30yr mortgage rate. Keep it concise (2-3 sentences)."
    )
    with st.spinner("Generating daily market brief..."):
        res = rag.query(question, metro_filter=metro_name)
    brief_text = res["answer"]
    generated_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
    upsert_brief_cache(metro_name, state, brief_date, brief_text, generated_at)
    return {
        "brief_text": brief_text,
        "generated_at": generated_at,
        "brief_date": brief_date,
        "from_cache": False,
    }


def main() -> None:
    st.set_page_config(page_title="HomeSignal", page_icon="🏠", layout="wide")
    if "tables_ensured" not in st.session_state:
        _ensure_ai_tables()
        st.session_state.tables_ensured = True

    st.markdown("""
<style>
@import url('https://fonts.googleapis.com/css2?family=Instrument+Serif:ital@0;1&family=IBM+Plex+Mono:wght@400;500&family=Inter:wght@400;500;600&display=swap');

html, body, [class*="css"] { font-family: 'Inter', sans-serif; font-size: 16px; line-height: 1.6; }

h1 {
    font-family: 'Instrument Serif', serif !important;
    font-size: 2.5rem !important;
    letter-spacing: -0.02em !important;
    color: #1C1C1E !important;
}
h2, h3 {
    font-family: 'Instrument Serif', serif !important;
    font-weight: 400 !important;
    font-size: 1.5rem !important;
    letter-spacing: -0.01em !important;
}

[data-testid="stMetricValue"] {
    font-family: 'IBM Plex Mono', monospace !important;
    font-size: 1.65rem !important;
    font-weight: 500 !important;
}
[data-testid="stMetricLabel"] {
    font-size: 0.8rem !important;
    font-weight: 600 !important;
    text-transform: uppercase !important;
    letter-spacing: 0.06em !important;
    color: #6B6B6B !important;
}
[data-testid="stMetricDelta"] {
    font-family: 'IBM Plex Mono', monospace !important;
    font-size: 0.85rem !important;
}
[data-testid="metric-container"] {
    background: #FFFFFF;
    border: 1px solid #E8E6E1;
    border-radius: 12px;
    padding: 16px 20px !important;
    box-shadow: 0 1px 4px rgba(0,0,0,0.06);
}

[data-testid="stSidebar"] { background: #EDECEA !important; border-right: 1px solid #DDD9D3 !important; }
[data-testid="stSidebar"] h1 { font-size: 1.8rem !important; }

[data-testid="stChatMessage"] {
    border-radius: 12px !important;
    border: 1px solid #E8E6E1 !important;
    background: #FFFFFF !important;
    padding: 4px 8px !important;
    margin-bottom: 8px !important;
}

.stButton > button {
    font-family: 'Inter', sans-serif !important;
    font-size: 0.9rem !important;
    font-weight: 600 !important;
    border-radius: 8px !important;
    border: 1.5px solid #0D7377 !important;
    color: #0D7377 !important;
    background: transparent !important;
    transition: all 0.15s ease;
}
.stButton > button:hover { background: #0D7377 !important; color: #FFFFFF !important; }

hr { border-color: #E8E6E1 !important; }
</style>
""", unsafe_allow_html=True)

    # ── Error guard: database ──────────────────────────────────────
    try:
        metros_df = get_metros()
    except Exception as e:
        st.error(f"Could not connect to database. Make sure `data/homesignal.db` exists.\n\n`{e}`")
        return

    metro_names = metros_df["metro_name"].tolist()
    if not metro_names:
        st.error("No metro data found in the database. Run the ingest pipeline first.")
        return

    # ── Sidebar ───────────────────────────────────────────────────
    with st.sidebar:
        st.title("HomeSignal")
        st.caption("AI-powered housing market intelligence")
        st.divider()

        last_updated = get_last_updated_date()
        if last_updated:
            st.caption(f"Data through: {last_updated}")

        default_metro = "Phoenix, AZ metro area"
        if default_metro not in metro_names:
            default_metro = metro_names[0]

        metro_name = st.selectbox(
            "Select a metro",
            metro_names,
            index=metro_names.index(default_metro) if default_metro in metro_names else 0,
        )
        st.divider()
        st.caption("Data sourced from Redfin & FRED. AI answers are for informational purposes only.")

        # Feedback analytics dashboard
        with st.expander("AI Feedback Stats", expanded=False):
            try:
                fb_df = _db_read_df(
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
                if not fb_df.empty:
                    total_up = int(fb_df["thumbs_up"].sum())
                    total_down = int(fb_df["thumbs_down"].sum())
                    st.caption(f"Total: {total_up} up / {total_down} down")
                    st.dataframe(
                        fb_df.rename(columns={
                            "metro": "Metro",
                            "thumbs_up": "Up",
                            "thumbs_down": "Down",
                            "total": "Total",
                        }),
                        use_container_width=True,
                        hide_index=True,
                    )
                else:
                    st.caption("No feedback recorded yet.")
            except Exception:
                st.caption("No feedback data available.")

    state = metros_df.loc[metros_df["metro_name"] == metro_name, "state"].iloc[0]

    # ── Main content header ────────────────────────────────────────
    st.title(metro_name)
    st.caption(f"Housing market intelligence · {state or ''}")

    # ── Error guard: RAG engine ────────────────────────────────────
    try:
        rag = get_rag()
    except Exception as e:
        st.error(f"AI engine failed to initialize. Check your `.env` file for `ANTHROPIC_API_KEY`.\n\n`{e}`")
        return

    # ---------------------------
    # Metric cards
    # ---------------------------
    latest = get_latest_metrics_for_metro(metro_name)
    if not latest:
        st.warning("No metrics found for the selected metro.")
        return

    mortgage = get_latest_mortgage_rate_with_mom()
    if mortgage:
        latest["mortgage_rate_30yr"] = mortgage.get("latest_rate")
        latest["mortgage_rate_30yr_mom_pct"] = mortgage.get("mom_pct")
    else:
        latest["mortgage_rate_30yr"] = None
        latest["mortgage_rate_30yr_mom_pct"] = None

    cards = st.columns(5, gap="small")

    with cards[0]:
        st.metric(
            "Median Sale Price",
            _format_money(latest.get("median_sale_price")),
            delta=(
                None
                if latest.get("median_sale_price_mom_pct") is None
                else f"{latest['median_sale_price_mom_pct']:.2f}%"
            ),
        )

    with cards[1]:
        st.metric(
            "Days on Market",
            _format_number(latest.get("days_on_market")),
            delta=(
                None
                if latest.get("days_on_market_mom_pct") is None
                else f"{latest['days_on_market_mom_pct']:.2f}%"
            ),
            delta_color="inverse",
        )

    with cards[2]:
        st.metric(
            "Inventory",
            _format_number(latest.get("inventory")),
            delta=(
                None
                if latest.get("inventory_mom_pct") is None
                else f"{latest['inventory_mom_pct']:.2f}%"
            ),
            delta_color="inverse",
        )

    with cards[3]:
        price_drop_display = _normalize_price_drop_pct_for_display(latest.get("price_drop_pct"))
        st.metric(
            "Price Drop %",
            _format_pct(price_drop_display),
            delta=(
                None
                if latest.get("price_drop_pct_mom_pct") is None
                else f"{latest['price_drop_pct_mom_pct']:.2f}%"
            ),
            delta_color="inverse",
        )

    with cards[4]:
        st.metric(
            "30yr Mortgage Rate",
            "N/A"
            if latest.get("mortgage_rate_30yr") is None
            else f"{latest['mortgage_rate_30yr']:.2f}%",
            delta=(
                None
                if latest.get("mortgage_rate_30yr_mom_pct") is None
                else f"{latest['mortgage_rate_30yr_mom_pct']:.2f}%"
            ),
            delta_color="inverse",
        )

    st.divider()

    # ---------------------------
    # Trend chart with AI hover tooltips
    # ---------------------------
    st.subheader("12-Month Trend (Median Sale Price)")
    trend_df = get_trend_series(metro_name, months=12)
    if trend_df.empty:
        st.warning("Not enough trend data to render the chart.")
        return

    # Generate/cached tooltip texts for hover.
    _pb = st.progress(0, text="Generating AI insights…")
    tooltip_texts, _ = get_or_create_tooltips(
        metro_name=metro_name, state=state, trend_df=trend_df, progress_bar=_pb
    )
    _pb.empty()

    fig = go.Figure()
    fig.add_trace(
        go.Scatter(
            x=trend_df["period_date"],
            y=trend_df["median_sale_price"],
            mode="lines+markers",
            line=dict(color="#0D7377", width=2.5),
            marker=dict(color="#0D7377", size=5, line=dict(color="#FFFFFF", width=1.5)),
            fill="tozeroy",
            fillcolor="rgba(13, 115, 119, 0.08)",
            # Plotly treats customdata as an array-like per point; using a 2D
            # shape keeps %{customdata[0]} stable.
            customdata=[[t] for t in tooltip_texts],
            hovertemplate=(
                "<b>%{x}</b><br>"
                "Median Sale Price: %{y:$,.0f}<br>"
                "<i>%{customdata[0]}</i><extra></extra>"
            ),
        )
    )

    fig.update_layout(
        margin=dict(l=0, r=0, t=16, b=0),
        height=380,
        paper_bgcolor="rgba(0,0,0,0)",
        plot_bgcolor="rgba(0,0,0,0)",
        xaxis=dict(
            title="",
            showgrid=False,
            tickfont=dict(family="DM Mono, monospace", size=11, color="#6B6B6B"),
            linecolor="#E8E6E1",
        ),
        yaxis=dict(
            title="",
            gridcolor="#E8E6E1",
            gridwidth=0.5,
            tickformat="$,.0f",
            tickfont=dict(family="DM Mono, monospace", size=11, color="#6B6B6B"),
            zeroline=False,
        ),
        hoverlabel=dict(
            bgcolor="#1C1C1E",
            font_color="#F7F6F3",
            font_family="Inter, sans-serif",
            font_size=13,
            bordercolor="#0D7377",
        ),
        font=dict(family="Inter, sans-serif", color="#1C1C1E"),
    )

    st.plotly_chart(fig, use_container_width=True)

    # ---------------------------
    # Daily AI market brief
    # ---------------------------
    st.subheader("Daily AI Market Brief")
    brief = get_or_create_daily_brief(rag, metro_name=metro_name, state=state)
    brief_text = brief["brief_text"]
    brief_text = re.sub(r"\[\d+\]", "", brief_text)
    brief_text = re.sub(r"<sup>\d+</sup>", "", brief_text)
    brief_text = brief_text.strip()
    brief_escaped = html.escape(brief_text).replace("$", "&#36;")
    st.markdown(f"""
<div style="
    background:#1C1C1E;
    border-left:4px solid #0D7377;
    border-radius:10px;
    padding:20px 24px;
    margin:4px 0 16px 0;
">
  <p style="margin:0;font-family:'Inter',sans-serif;font-size:1rem;
     line-height:1.75;color:#F0EDE8;">{brief_escaped}</p>
  <p style="margin:14px 0 0 0;font-size:0.78rem;color:#888;
     font-family:'IBM Plex Mono',monospace;">Generated {brief['generated_at']}</p>
</div>
""", unsafe_allow_html=True)

    st.divider()

    # ---------------------------
    # RAG chat + feedback
    # ---------------------------
    st.subheader("Ask HomeSignal")

    if "chat_histories" not in st.session_state:
        st.session_state.chat_histories = {}
    if metro_name not in st.session_state.chat_histories:
        st.session_state.chat_histories[metro_name] = []
    chat_history = st.session_state.chat_histories[metro_name]

    # Render chat history.
    for i, msg in enumerate(chat_history):
        with st.chat_message(msg["role"]):
            content = msg.get("content", "")
            if msg.get("role") == "assistant":
                st.markdown(
                    _render_chat_answer_preserving_dollars(content),
                    unsafe_allow_html=True,
                )
            else:
                st.write(content)
            if msg["role"] == "assistant":
                if msg.get("sources"):
                    with st.expander("Sources", expanded=False):
                        src_lines = "\n".join(
                            f"{idx}. {s}" for idx, s in enumerate(msg["sources"], start=1)
                        )
                        st.markdown(src_lines)

                # If feedback already recorded, display it.
                if msg.get("feedback") in ("up", "down"):
                    st.caption(f"Feedback: {msg['feedback']}")

                # Feedback buttons
                if msg.get("feedback") is None:
                    fb_key = f"fb_{metro_name}_{i}"
                    cols = st.columns([1, 1, 6])
                    with cols[0]:
                        if st.button("👍", key=f"up_{fb_key}"):
                            rag.log_feedback(
                                question=msg.get("question", ""),
                                answer=msg["content"],
                                feedback="up",
                                metro=metro_name,
                            )
                            msg["feedback"] = "up"
                            chat_history[i] = msg
                            st.rerun()
                    with cols[1]:
                        if st.button("👎", key=f"down_{fb_key}"):
                            rag.log_feedback(
                                question=msg.get("question", ""),
                                answer=msg["content"],
                                feedback="down",
                                metro=metro_name,
                            )
                            msg["feedback"] = "down"
                            chat_history[i] = msg
                            st.rerun()

    # Chat input at bottom.
    user_question = st.chat_input("Ask about your metro market...")
    if user_question:
        chat_history.append({"role": "user", "content": user_question})

        with st.chat_message("user"):
            st.write(user_question)

        with st.chat_message("assistant"):
            with st.spinner("Generating response..."):
                # Build conversation history for RAG (exclude the message we just appended)
                history_for_rag = [
                    {"role": msg["role"], "content": msg["content"]}
                    for msg in chat_history[:-1]
                    if msg.get("content")
                ]
                # Let RAGEngine auto-detect metros; no explicit filter for chat
                res = rag.query(
                    user_question,
                    metro_filter=None,
                    conversation_history=history_for_rag,
                )
                answer = res["answer"]
                sources = res.get("sources") or []
                cleaned_answer = _answer_with_superscript_citations(answer)

                st.markdown(
                    _render_chat_answer_preserving_dollars(cleaned_answer),
                    unsafe_allow_html=True,
                )
                with st.expander("Sources", expanded=False):
                    src_lines = "\n".join(f"{i}. {s}" for i, s in enumerate(sources, start=1))
                    st.markdown(src_lines)

        chat_history.append(
            {
                "role": "assistant",
                "content": cleaned_answer,
                "sources": sources,
                "question": user_question,
                "feedback": None,
            }
        )
        # Cap history at 50 messages to prevent unbounded growth.
        if len(chat_history) > 50:
            st.session_state.chat_histories[metro_name] = chat_history[-50:]


if __name__ == "__main__":
    main()

