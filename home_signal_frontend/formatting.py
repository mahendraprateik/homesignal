"""
HomeSignal display formatting helpers.

Pure functions with no database, Streamlit, or backend dependencies.
Used by the frontend for rendering values, cleaning AI output, etc.
"""

from __future__ import annotations

import re
from typing import Any, Optional

import pandas as pd


def format_money(x: Any) -> str:
    if x is None or pd.isna(x):
        return "N/A"
    try:
        v = float(x)
        return f"${v:,.0f}"
    except Exception:
        return "N/A"


def format_number(x: Any) -> str:
    if x is None or pd.isna(x):
        return "N/A"
    try:
        return f"{float(x):,.0f}"
    except Exception:
        return "N/A"


def format_pct(x: Any) -> str:
    if x is None or pd.isna(x):
        return "N/A"
    try:
        return f"{float(x):.2f}%"
    except Exception:
        return "N/A"


def normalize_price_drop_pct_for_display(x: Any) -> Optional[float]:
    """
    Redfin's PRICE_DROPS can be stored either as 0-100 or 0-1.
    Normalise to 0-100 for UI display.
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


def answer_with_superscript_citations(answer: str) -> str:
    """
    Remove ALL citation markers from an answer text.

    Strips bracket citations [1], superscript <sup>1</sup>,
    and trailing "Sources: ..." fallback lines.
    """
    if not answer:
        return ""
    answer = re.sub(r"(?im)^\s*Sources:\s*.*$", "", answer).strip()
    answer = re.sub(r"<sup>\s*\d+\s*</sup>", "", answer, flags=re.IGNORECASE)
    answer = re.sub(r"\[\s*\d+\s*\]", "", answer)
    answer = re.sub(r"\s{2,}", " ", answer).strip()
    return answer


def render_chat_answer_preserving_dollars(text: str) -> str:
    """
    Prepare an answer for st.markdown rendering while preserving dollar signs
    and preventing LaTeX parsing.
    """
    cleaned = answer_with_superscript_citations(text)
    cleaned = cleaned.replace("$", "\\$")
    return cleaned


def clean_brief_for_html(text: str) -> str:
    """
    Convert markdown artifacts in AI brief text to clean HTML.

    Handles: **bold** → <strong>, | → —, leading header lines like
    "**HomeSignal Daily Brief — ...**" are stripped entirely since the
    UI already has its own header.
    """
    if not text:
        return ""
    # Strip leading header line (e.g. "**HomeSignal Daily Brief — Phoenix, AZ Metro | March 29, 2026**")
    text = re.sub(
        r"^\s*\*{0,2}HomeSignal\s+Daily\s+Brief[^*]*\*{0,2}\s*",
        "",
        text,
        flags=re.IGNORECASE,
    ).strip()
    # Convert **bold** to <strong>
    text = re.sub(r"\*\*(.+?)\*\*", r"<strong>\1</strong>", text)
    # Remove any remaining stray * used for emphasis
    text = re.sub(r"\*(.+?)\*", r"<em>\1</em>", text)
    # Replace pipe separators with em-dash
    text = text.replace(" | ", " — ").replace("|", " — ")
    # Collapse multiple spaces / newlines
    text = re.sub(r"\s{2,}", " ", text).strip()
    return text


def truncate_tooltip_text(text: str, max_chars: int = 150) -> str:
    """Truncate tooltip text to max_chars, appending '...' when truncated."""
    if text is None:
        return ""
    s = str(text).strip()
    if len(s) <= max_chars:
        return s
    if max_chars <= 3:
        return s[:max_chars]
    return s[: max_chars - 3].rstrip() + "..."
