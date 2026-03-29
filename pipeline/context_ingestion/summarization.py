"""
Step 6: Summarization.

Extractive summarization — no LLM call needed.
Produces a 1–2 sentence summary and 2–4 data-rich key points per chunk.
"""

from __future__ import annotations

import re
from typing import Any, Dict, List


def summarize_chunk(text: str) -> Dict[str, Any]:
    """
    Generate an extractive summary and key points from a chunk.
    Uses the first 1-2 sentences as summary and extracts key sentences.
    """
    sentences = re.split(r"(?<=[.!?])\s+", text.strip())
    sentences = [s.strip() for s in sentences if len(s.strip()) > 20]

    # Summary: first 1-2 sentences
    summary_sents = sentences[:2] if len(sentences) >= 2 else sentences[:1]
    summary = " ".join(summary_sents) if summary_sents else text[:200]

    # Key points: sentences containing numbers or strong signal words
    key_points: List[str] = []
    for sent in sentences:
        has_number = bool(re.search(r"\d+\.?\d*\s*%|\$\s*[\d,]+|\d{1,3}(?:,\d{3})+", sent))
        has_signal = any(
            w in sent.lower()
            for w in ["increased", "decreased", "rose", "fell", "record", "highest", "lowest"]
        )
        if (has_number or has_signal) and sent not in summary_sents:
            key_points.append(sent)
            if len(key_points) >= 4:
                break

    # Ensure at least 2 key points
    if len(key_points) < 2:
        for sent in sentences:
            if sent not in summary_sents and sent not in key_points:
                key_points.append(sent)
                if len(key_points) >= 2:
                    break

    return {"summary": summary, "key_points": key_points[:4]}
