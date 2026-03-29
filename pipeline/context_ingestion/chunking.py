"""
Step 4: Semantic chunking.

Splits normalized article text into 80–250 word chunks.
Respects paragraph and section boundaries.
One coherent idea per chunk where possible.
"""

from __future__ import annotations

import re
from typing import List


def semantic_chunk(text: str, min_words: int, max_words: int) -> List[str]:
    """
    Split text into chunks of min_words–max_words, respecting paragraph boundaries.
    """
    paragraphs = [p.strip() for p in text.split("\n\n") if p.strip()]
    chunks: List[str] = []
    current: List[str] = []
    current_word_count = 0

    for para in paragraphs:
        para_words = len(para.split())

        # If a single paragraph exceeds max, split it by sentences
        if para_words > max_words:
            if current:
                chunks.append("\n\n".join(current))
                current = []
                current_word_count = 0

            sentences = re.split(r"(?<=[.!?])\s+", para)
            sent_buf: List[str] = []
            sent_wc = 0
            for sent in sentences:
                sw = len(sent.split())
                if sent_wc + sw > max_words and sent_buf:
                    chunks.append(" ".join(sent_buf))
                    sent_buf = []
                    sent_wc = 0
                sent_buf.append(sent)
                sent_wc += sw
            if sent_buf:
                chunks.append(" ".join(sent_buf))
            continue

        # Would adding this paragraph exceed max?
        if current_word_count + para_words > max_words and current:
            chunks.append("\n\n".join(current))
            current = []
            current_word_count = 0

        current.append(para)
        current_word_count += para_words

    # Flush remainder
    if current:
        chunks.append("\n\n".join(current))

    # Merge undersized trailing chunk into previous
    if len(chunks) >= 2 and len(chunks[-1].split()) < min_words:
        chunks[-2] = chunks[-2] + "\n\n" + chunks[-1]
        chunks.pop()

    return [c for c in chunks if len(c.split()) >= min_words // 2]
