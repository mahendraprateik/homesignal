"""
HomeSignal Semantic Model loader.

Reads data/semantic_model.yaml and provides typed access to metric definitions
for use across the entire stack:

- Pipeline (vector document generation, metric definition grounding doc)
- RAG engine (grounding context appended to every query)
- Chat engine (SQL tool allowlists, tool descriptions)
- Frontend via api.py (dashboard card config, display labels, formatting)

Usage:
    from backend.semantic_model import get_semantic_model
    model = get_semantic_model()
    model.grounding_text()                  # for RAG / vector docs
    model.rankable_metric_names()           # for chat engine allowlist
    model.dashboard_cards()                 # ordered card definitions
    model.redfin_metric("median_sale_price")  # single metric lookup
"""

from __future__ import annotations

import os
from functools import lru_cache
from typing import Any, Dict, List, Optional

import yaml


_SEMANTIC_MODEL_PATH = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "data",
    "semantic_model.yaml",
)


class SemanticModel:
    """In-memory representation of the semantic model YAML."""

    def __init__(self, data: Dict[str, Any]) -> None:
        self._data = data
        self._redfin: Dict[str, Dict[str, Any]] = data.get("redfin_metrics", {})
        self._fred: Dict[str, Dict[str, Any]] = data.get("fred_metrics", {})

    # ------------------------------------------------------------------
    # Single-metric lookup
    # ------------------------------------------------------------------

    def redfin_metric(self, key: str) -> Dict[str, Any]:
        """Return the definition dict for a Redfin metric key."""
        return self._redfin.get(key, {})

    def fred_metric(self, series_id: str) -> Dict[str, Any]:
        """Return the definition dict for a FRED series."""
        return self._fred.get(series_id, {})

    # ------------------------------------------------------------------
    # Filtered collections
    # ------------------------------------------------------------------

    def primary_redfin_metrics(self) -> Dict[str, Dict[str, Any]]:
        """Non-derived Redfin metrics (excludes MoM/YoY helper columns)."""
        return {
            k: v for k, v in self._redfin.items()
            if not v.get("derived", False)
        }

    def rankable_metric_names(self) -> set:
        """Metric keys allowed in the top_metros_by_metric SQL tool."""
        return {
            k for k, v in self._redfin.items()
            if v.get("rankable", False)
        }

    def dashboard_cards(self) -> List[Dict[str, Any]]:
        """
        Return metric definitions that should appear as dashboard cards,
        ordered by dashboard_position. Includes both Redfin and FRED metrics.
        """
        cards: List[Dict[str, Any]] = []
        for key, defn in self._redfin.items():
            if defn.get("dashboard_card"):
                cards.append({"key": key, "source": "redfin", **defn})
        for key, defn in self._fred.items():
            if defn.get("dashboard_card"):
                cards.append({"key": key, "source": "fred", **defn})
        cards.sort(key=lambda c: c.get("dashboard_position", 999))
        return cards

    # ------------------------------------------------------------------
    # Grounding text (for RAG context and vector metric-definition doc)
    # ------------------------------------------------------------------

    def grounding_text(self) -> str:
        """
        Build the metric definitions grounding document used by RAG and
        embedded in the ChromaDB vector store.

        Returns a human-readable block covering all metrics with their
        descriptions, units, and sources.
        """
        lines = ["HomeSignal metric definitions (grounding rules):"]
        lines.append("")
        lines.append("Redfin housing metrics (monthly, from Redfin Metro Market Tracker):")
        for key, defn in self.primary_redfin_metrics().items():
            desc = defn.get("description", key)
            unit = defn.get("unit", "")
            lines.append(f"  {key}: {desc} Unit: {unit}.")

        lines.append("")
        lines.append("FRED macroeconomic indicators (from Federal Reserve Economic Data):")
        for series_id, defn in self._fred.items():
            desc = defn.get("description", series_id)
            cadence = defn.get("update_cadence", "periodic")
            lines.append(f"  {series_id}: {desc} Updated {cadence}.")

        lines.append("")
        lines.append(
            "Notes: All Redfin housing metrics come from monthly snapshots. "
            "FRED data is available via direct SQL query at chat time, not "
            "embedded in the vector store."
        )
        return "\n".join(lines)

    # ------------------------------------------------------------------
    # Tool description helpers (for chat engine)
    # ------------------------------------------------------------------

    def redfin_metrics_summary_for_tools(self) -> str:
        """One-line-per-metric summary for SQL tool descriptions."""
        parts = []
        for key, defn in self.primary_redfin_metrics().items():
            parts.append(f"{key} ({defn.get('display_name', key)})")
        return ", ".join(parts)

    # ------------------------------------------------------------------
    # Vector document helpers (for pipeline/update_vectors.py)
    # ------------------------------------------------------------------

    def vector_metric_configs(self) -> List[Dict[str, Any]]:
        """
        Return ordered list of primary metric configs for vector document generation.
        Each entry has: key, display_name, vector_format_digits, vector_prefix,
        vector_suffix, mom_column, yoy_column.
        """
        configs = []
        for key, defn in self.primary_redfin_metrics().items():
            configs.append({
                "key": key,
                "display_name": defn.get("display_name", key),
                "digits": defn.get("vector_format_digits", 2),
                "prefix": defn.get("vector_prefix", ""),
                "suffix": defn.get("vector_suffix", ""),
                "mom_column": defn.get("mom_column"),
                "yoy_column": defn.get("yoy_column"),
            })
        return configs

    # ------------------------------------------------------------------
    # Ingestion helpers (for pipeline/data_ingestion.py)
    # ------------------------------------------------------------------

    def redfin_column_rename_map(self) -> Dict[str, str]:
        """
        Build the Redfin TSV source_column → SQLite column rename dict.

        E.g. {"MEDIAN_SALE_PRICE": "median_sale_price", "MEDIAN_DOM": "days_on_market", ...}
        """
        return {
            defn["source_column"]: key
            for key, defn in self._redfin.items()
            if "source_column" in defn
        }

    def redfin_sqlite_columns(self) -> List[str]:
        """
        Return ordered list of Redfin metric column names for SQLite CREATE TABLE.
        All are REAL type. Excludes structural columns (period_date, metro_name, etc).
        """
        return list(self.redfin_column_rename_map().values())

    def fred_series_ids(self) -> List[str]:
        """Return the list of FRED series IDs to ingest."""
        return list(self._fred.keys())

    # ------------------------------------------------------------------
    # Raw access
    # ------------------------------------------------------------------

    @property
    def redfin(self) -> Dict[str, Dict[str, Any]]:
        return self._redfin

    @property
    def fred(self) -> Dict[str, Dict[str, Any]]:
        return self._fred


@lru_cache(maxsize=1)
def get_semantic_model(path: Optional[str] = None) -> SemanticModel:
    """
    Load and cache the semantic model from YAML.

    Cached via lru_cache so repeated calls across modules share one instance.
    """
    yaml_path = path or _SEMANTIC_MODEL_PATH
    if not os.path.exists(yaml_path):
        raise FileNotFoundError(
            f"Semantic model not found at {yaml_path}. "
            "Ensure data/semantic_model.yaml exists."
        )
    with open(yaml_path, "r") as f:
        data = yaml.safe_load(f)
    return SemanticModel(data)
