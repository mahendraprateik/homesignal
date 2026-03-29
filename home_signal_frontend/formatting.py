"""
HomeSignal display formatting helpers.

Pure functions with no database, Streamlit, or backend dependencies.
Used by the frontend for rendering values, cleaning AI output, etc.
"""

from __future__ import annotations

import math
import re
from typing import Any, Optional


def _is_na(x: Any) -> bool:
    """Check if a value is None or NaN without requiring pandas."""
    if x is None:
        return True
    try:
        return isinstance(x, float) and math.isnan(x)
    except (TypeError, ValueError):
        return False


def format_money(x: Any) -> str:
    if _is_na(x):
        return "N/A"
    try:
        v = float(x)
        return f"${v:,.0f}"
    except Exception:
        return "N/A"


def format_number(x: Any) -> str:
    if _is_na(x):
        return "N/A"
    try:
        return f"{float(x):,.0f}"
    except Exception:
        return "N/A"


def format_pct(x: Any) -> str:
    if _is_na(x):
        return "N/A"
    try:
        return f"{float(x):.2f}%"
    except Exception:
        return "N/A"


def normalize_price_drop_pct_for_display(x: Any) -> Optional[float]:
    """
    Redfin's PRICE_DROPS can be stored either as 0-100 or 0-1.
    Normalise to 0-100 for UI display.

    Note: Values below 1.0 are assumed to be ratios (0-1 scale).
    Legitimate percentages below 1% (e.g. 0.5%) are uncommon in
    Redfin price-drop data and will be scaled up.
    """
    if _is_na(x):
        return None
    try:
        v = float(x)
        if v < 1.0:
            v = v * 100.0
        return v
    except Exception:
        return None


# Re-export from shared module to maintain backward compatibility for frontend imports
from backend.formatting_utils import answer_with_superscript_citations  # noqa: F401


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

    HTML-escapes the input first to prevent XSS via AI-generated content.
    """
    if not text:
        return ""
    # HTML-escape first to prevent injection via AI output
    import html as _html
    text = _html.escape(text)
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


from backend.formatting_utils import truncate_tooltip_text  # noqa: F401
