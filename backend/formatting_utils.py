"""
Shared formatting utilities used by both backend.api and home_signal_frontend.

Extracted from home_signal_frontend.formatting to break the circular
dependency where backend.api was importing from the frontend layer.
"""

from __future__ import annotations

import re


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
    # Collapse runs of 3+ newlines to 2 (preserve paragraph breaks & markdown structure)
    answer = re.sub(r"\n{3,}", "\n\n", answer)
    # Collapse horizontal whitespace only (spaces/tabs), not newlines
    answer = re.sub(r"[^\S\n]{2,}", " ", answer).strip()
    return answer


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
