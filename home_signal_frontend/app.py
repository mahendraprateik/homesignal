"""
HomeSignal Streamlit frontend.

This module contains ONLY the Streamlit UI layer. All data access and AI
operations are delegated to ``backend.api``. Display formatting lives in
``home_signal_frontend.formatting``.

Run:
    ./venv/bin/streamlit run home_signal_frontend/app.py
"""

from __future__ import annotations

import os
import sys
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

# Ensure the project root is on sys.path so backend/ is importable.
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import streamlit as st

import backend.api as api
from home_signal_frontend.formatting import (
    answer_with_superscript_citations,
    clean_brief_for_html,
    format_money,
    format_number,
    format_pct,
    normalize_price_drop_pct_for_display,
    render_chat_answer_preserving_dollars,
)


# ---------------------------------------------------------------------------
# Streamlit-cached wrappers around backend.api
# ---------------------------------------------------------------------------

@st.cache_data(ttl=3600)
def cached_get_metros():
    return api.get_metros()


@st.cache_data(ttl=3600)
def cached_get_last_updated_date():
    return api.get_last_updated_date()


@st.cache_data(ttl=300)
def cached_get_data_freshness():
    return api.get_data_freshness()


@st.cache_data(ttl=3600)
def cached_get_latest_metrics(metro_name: str):
    return api.get_latest_metrics_for_metro(metro_name)


@st.cache_data(ttl=3600)
def cached_get_mortgage_rate():
    return api.get_latest_mortgage_rate_with_mom()


@st.cache_data(ttl=3600)
def cached_get_trend_series(metro_name: str, months: int = 12):
    return api.get_trend_series(metro_name, months=months)


@st.cache_data(ttl=900)
def cached_maybe_sync_cloud_data():
    return api.maybe_sync_cloud_data()


# ---------------------------------------------------------------------------
# CSS
# ---------------------------------------------------------------------------

_CUSTOM_CSS = """
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

/* ── Chat answer typography ── */
[data-testid="stChatMessage"] p {
    margin: 0 0 0.7em 0 !important;
    line-height: 1.7 !important;
    color: #2C2C2E;
}
[data-testid="stChatMessage"] p:last-child { margin-bottom: 0 !important; }

[data-testid="stChatMessage"] h3,
[data-testid="stChatMessage"] h4 {
    font-family: 'Inter', sans-serif !important;
    font-weight: 600 !important;
    font-size: 0.95rem !important;
    color: #1C1C1E !important;
    margin: 1.1em 0 0.4em 0 !important;
    letter-spacing: 0.01em !important;
}
[data-testid="stChatMessage"] h3:first-child,
[data-testid="stChatMessage"] h4:first-child { margin-top: 0 !important; }

[data-testid="stChatMessage"] ul,
[data-testid="stChatMessage"] ol {
    margin: 0.3em 0 0.8em 0 !important;
    padding-left: 1.4em !important;
}
[data-testid="stChatMessage"] li {
    margin-bottom: 0.35em !important;
    line-height: 1.65 !important;
    color: #2C2C2E;
}
[data-testid="stChatMessage"] li::marker {
    color: #0D7377;
}

[data-testid="stChatMessage"] strong {
    font-weight: 600;
    color: #1C1C1E;
}

/* ── Chat tables ── */
[data-testid="stChatMessage"] table {
    width: 100%;
    border-collapse: separate;
    border-spacing: 0;
    margin: 0.6em 0 1em 0;
    font-size: 0.88rem;
    border: 1px solid #E0DDD8;
    border-radius: 8px;
    overflow: hidden;
}
[data-testid="stChatMessage"] thead th {
    background: #F5F3F0;
    font-family: 'Inter', sans-serif;
    font-weight: 600;
    font-size: 0.8rem;
    text-transform: uppercase;
    letter-spacing: 0.04em;
    color: #5A5A5A;
    padding: 10px 14px;
    text-align: left;
    border-bottom: 2px solid #0D7377;
}
[data-testid="stChatMessage"] tbody td {
    padding: 9px 14px;
    border-bottom: 1px solid #EDEBE7;
    color: #2C2C2E;
    font-family: 'IBM Plex Mono', monospace;
    font-size: 0.86rem;
}
[data-testid="stChatMessage"] tbody tr:last-child td {
    border-bottom: none;
}
[data-testid="stChatMessage"] tbody tr:hover {
    background: rgba(13, 115, 119, 0.04);
}

/* ── Inline code in chat ── */
[data-testid="stChatMessage"] code {
    background: #F5F3F0;
    padding: 2px 6px;
    border-radius: 4px;
    font-family: 'IBM Plex Mono', monospace;
    font-size: 0.85em;
    color: #0D7377;
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
"""


# ---------------------------------------------------------------------------
# Rendering helpers
# ---------------------------------------------------------------------------

def _render_sidebar(metro_names: List[str], metros_df) -> str:
    """Render the sidebar and return the selected metro_name."""
    with st.sidebar:
        st.title("HomeSignal")
        st.caption("AI-powered housing market intelligence")
        st.divider()

        last_updated = cached_get_last_updated_date()
        if last_updated:
            st.caption(f"Data through: {last_updated}")

        # Data freshness & refresh
        with st.expander("Data Freshness", expanded=False):
            freshness = cached_get_data_freshness()
            fred_info = freshness.get("fred", {})
            redfin_info = freshness.get("redfin", {})

            def _staleness_label(period_str: Optional[str], stale_days: int) -> Tuple[str, str, bool]:
                if not period_str:
                    return "stale", "No data", True
                try:
                    period_dt = datetime.strptime(str(period_str)[:10], "%Y-%m-%d").date()
                    days_old = (datetime.now(timezone.utc).date() - period_dt).days
                    is_stale = days_old > stale_days
                    label = "stale" if is_stale else "fresh"
                    return label, f"{days_old}d ago", is_stale
                except Exception:
                    return "unknown", "unknown", False

            fred_label, fred_age, fred_stale = _staleness_label(fred_info.get("latest_period"), 8)
            redfin_label, redfin_age, redfin_stale = _staleness_label(redfin_info.get("latest_period"), 35)

            fred_color = "#D32F2F" if fred_stale else "#2E7D32"
            redfin_color = "#D32F2F" if redfin_stale else "#2E7D32"

            st.markdown(
                f'<span style="color:{fred_color};font-weight:600;">FRED</span> '
                f'— {fred_info.get("latest_period", "N/A")} ({fred_age})',
                unsafe_allow_html=True,
            )
            st.markdown(
                f'<span style="color:{redfin_color};font-weight:600;">Redfin</span> '
                f'— {redfin_info.get("latest_period", "N/A")} ({redfin_age})',
                unsafe_allow_html=True,
            )

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

        # Feedback analytics
        with st.expander("AI Feedback Stats", expanded=False):
            try:
                fb_df = api.get_feedback_stats()
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

    return metro_name


def _render_metric_cards(latest: Dict[str, Any]) -> None:
    """Render dashboard metric cards driven by the semantic model."""
    period = latest.get("period_date", "")
    if period:
        try:
            pd_dt = datetime.strptime(str(period)[:10], "%Y-%m-%d")
            st.caption(f"Latest data: {pd_dt.strftime('%B %Y')}")
        except ValueError:
            st.caption(f"Latest data: {period}")

    card_defs = api.get_dashboard_card_config()
    cols = st.columns(len(card_defs), gap="small")

    _FORMAT_FN = {
        "money": format_money,
        "number": format_number,
        "pct": format_pct,
        "rate": lambda v: "N/A" if v is None else f"{v:.2f}%",
    }

    for col, card in zip(cols, card_defs):
        key = card["key"]
        fmt = card.get("format", "number")
        delta_color = card.get("delta_color", "normal")
        formatter = _FORMAT_FN.get(fmt, format_number)

        # Determine value and delta keys based on source
        if card["source"] == "fred":
            value = latest.get(f"{key}")
            delta_val = latest.get(f"{key}_mom_pct")
        else:
            value = latest.get(key)
            delta_val = latest.get(f"{key}_mom_pct")

        # Apply display normalization (e.g. price_drop_pct 0-1 → 0-100)
        if card.get("display_normalize") and fmt == "pct":
            value = normalize_price_drop_pct_for_display(value)

        with col:
            st.metric(
                card["display_name"],
                formatter(value),
                delta=None if delta_val is None else f"{delta_val:.2f}% MoM",
                delta_color=delta_color,
            )


def _render_trend_chart(metro_name: str, state: Optional[str]) -> None:
    """Render the 12-month trend chart with AI hover tooltips."""
    import plotly.graph_objects as go

    st.subheader("12-Month Trend (Median Sale Price)")
    trend_df = cached_get_trend_series(metro_name, months=12)
    if trend_df.empty:
        st.warning("Not enough trend data to render the chart.")
        return

    # Generate/cache tooltip texts with a progress bar.
    _pb = st.progress(0, text="Generating AI insights…")

    def _on_progress(completed: int, total: int) -> None:
        _pb.progress(
            min(1.0, completed / max(total, 1)),
            text=f"Generating AI insights… {completed}/{total}",
        )

    tooltip_texts, _ = api.get_or_create_tooltips(
        metro_name=metro_name, state=state, trend_df=trend_df, on_progress=_on_progress,
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


def _render_daily_brief(metro_name: str, state: Optional[str]) -> None:
    """Render the daily AI market brief card."""
    st.subheader("Daily AI Market Brief")

    with st.spinner("Generating daily market brief..."):
        brief = api.get_or_create_daily_brief(metro_name=metro_name, state=state)

    brief_text = answer_with_superscript_citations(brief["brief_text"])
    brief_escaped = clean_brief_for_html(brief_text).replace("$", "&#36;")
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


def _render_chat(metro_name: str) -> None:
    """Render the RAG chat interface with feedback buttons."""
    st.subheader("Ask HomeSignal")

    if "chat_histories" not in st.session_state:
        st.session_state.chat_histories = {}
    if metro_name not in st.session_state.chat_histories:
        st.session_state.chat_histories[metro_name] = []
    chat_history = st.session_state.chat_histories[metro_name]

    def _dedupe_sources(values: Optional[List[str]]) -> List[str]:
        deduped: List[str] = []
        seen = set()
        for src in values or []:
            s = (src or "").strip()
            if not s or s in seen:
                continue
            seen.add(s)
            deduped.append(s)
        return deduped

    # Render existing messages.
    for i, msg in enumerate(chat_history):
        with st.chat_message(msg["role"]):
            content = msg.get("content", "")
            if msg.get("role") == "assistant":
                st.markdown(render_chat_answer_preserving_dollars(content))
            else:
                st.write(content)

            if msg["role"] == "assistant":
                if msg.get("sources"):
                    with st.expander("Sources", expanded=False):
                        src_lines = "\n".join(
                            f"{idx}. {s}" for idx, s in enumerate(msg["sources"], start=1)
                        )
                        st.markdown(src_lines)

                if msg.get("feedback") in ("up", "down"):
                    st.caption(f"Feedback: {msg['feedback']}")

                if msg.get("feedback") is None:
                    fb_key = f"fb_{metro_name}_{i}"
                    cols = st.columns([1, 1, 6])
                    with cols[0]:
                        if st.button("\U0001f44d", key=f"up_{fb_key}"):
                            api.log_feedback(
                                question=msg.get("question", ""),
                                answer=msg["content"],
                                feedback="up",
                                metro=metro_name,
                            )
                            msg["feedback"] = "up"
                            chat_history[i] = msg
                            st.rerun()
                    with cols[1]:
                        if st.button("\U0001f44e", key=f"down_{fb_key}"):
                            api.log_feedback(
                                question=msg.get("question", ""),
                                answer=msg["content"],
                                feedback="down",
                                metro=metro_name,
                            )
                            msg["feedback"] = "down"
                            chat_history[i] = msg
                            st.rerun()

    # Chat input.
    user_question = st.chat_input("Ask about your metro market...")
    if user_question:
        chat_history.append({"role": "user", "content": user_question})

        with st.chat_message("user"):
            st.write(user_question)

        with st.chat_message("assistant"):
            with st.spinner("Generating response..."):
                history_for_chat = [
                    {"role": msg["role"], "content": msg["content"]}
                    for msg in chat_history[:-1]
                    if msg.get("content")
                ]
                try:
                    res = api.chat(
                        user_question,
                        conversation_history=history_for_chat,
                        metro_filter=metro_name,
                    )
                    answer = res["answer"]
                    sources = _dedupe_sources(res.get("sources"))
                except Exception:
                    answer = (
                        "I hit a temporary issue while generating this answer. "
                        "Please try again in a moment."
                    )
                    sources = []

                st.markdown(render_chat_answer_preserving_dollars(answer))
                with st.expander("Sources", expanded=False):
                    src_lines = "\n".join(f"{i}. {s}" for i, s in enumerate(sources, start=1))
                    st.markdown(src_lines if src_lines else "No sources available.")

        chat_history.append(
            {
                "role": "assistant",
                "content": answer_with_superscript_citations(answer),
                "sources": sources,
                "question": user_question,
                "feedback": None,
            }
        )
        if len(chat_history) > 50:
            st.session_state.chat_histories[metro_name] = chat_history[-50:]


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> None:
    st.set_page_config(page_title="HomeSignal", page_icon="\U0001f3e0", layout="wide")

    # Optional cloud snapshot sync (if configured via env vars).
    # This keeps interactive refresh out of the UI while allowing background
    # scheduled jobs to publish newer data artifacts.
    try:
        sync_result = cached_maybe_sync_cloud_data()
        if sync_result.get("updated"):
            st.cache_data.clear()
            st.cache_resource.clear()
    except Exception:
        # Avoid blocking app startup if cloud sync has a transient issue.
        pass

    if "tables_ensured" not in st.session_state:
        api.ensure_ai_tables()
        st.session_state.tables_ensured = True

    st.markdown(_CUSTOM_CSS, unsafe_allow_html=True)

    # Error guard: database
    try:
        metros_df = cached_get_metros()
    except Exception as e:
        st.error(f"Could not connect to database. Make sure `data/homesignal.db` exists.\n\n`{e}`")
        return

    metro_names = metros_df["metro_name"].tolist()
    if not metro_names:
        st.error("No metro data found in the database. Run the ingest pipeline first.")
        return

    # Sidebar (returns selected metro)
    metro_name = _render_sidebar(metro_names, metros_df)
    state = metros_df.loc[metros_df["metro_name"] == metro_name, "state"].iloc[0]

    # Main content header
    st.title(metro_name)
    st.caption(f"Housing market intelligence · {state or ''}")

    # Metric cards
    latest = cached_get_latest_metrics(metro_name)
    if not latest:
        st.warning("No metrics found for the selected metro.")
        return

    mortgage = cached_get_mortgage_rate()
    if mortgage:
        latest["MORTGAGE30US"] = mortgage.get("latest_rate")
        latest["MORTGAGE30US_mom_pct"] = mortgage.get("mom_pct")
    else:
        latest["MORTGAGE30US"] = None
        latest["MORTGAGE30US_mom_pct"] = None

    _render_metric_cards(latest)
    st.divider()

    # Trend chart
    _render_trend_chart(metro_name, state)

    # Daily AI brief
    _render_daily_brief(metro_name, state)
    st.divider()

    # RAG chat
    _render_chat(metro_name)


if __name__ == "__main__":
    main()
