"""Shared data models for context ingestion pipeline."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict


@dataclass
class ProcessedChunk:
    document_id: str
    chunk_text: str
    metadata: Dict[str, Any]
