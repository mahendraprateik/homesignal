"""
Tests for the HomeSignal semantic model.

Validates that data/semantic_model.yaml is well-formed and that all
consumers (pipeline, RAG, chat engine, API, frontend) receive correct
data from the loader.

Run:
    python -m pytest tests/test_semantic_model.py -v
"""

import pytest

from backend.semantic_model import get_semantic_model, SemanticModel


@pytest.fixture(scope="module")
def model() -> SemanticModel:
    return get_semantic_model()


# ── YAML structure ──


class TestYAMLStructure:
    def test_loads_without_error(self, model):
        assert model is not None

    def test_has_redfin_metrics(self, model):
        assert len(model.redfin) > 0

    def test_has_fred_metrics(self, model):
        assert len(model.fred) > 0

    def test_nine_primary_redfin_metrics(self, model):
        assert len(model.primary_redfin_metrics()) == 9

    def test_four_fred_series(self, model):
        assert len(model.fred) == 4

    def test_derived_metrics_excluded_from_primary(self, model):
        primary_keys = set(model.primary_redfin_metrics().keys())
        assert "price_mom" not in primary_keys
        assert "price_yoy" not in primary_keys
        assert "inventory_mom" not in primary_keys


# ── Required fields per metric ──


class TestRequiredFields:
    REQUIRED_REDFIN = [
        "display_name", "description", "unit", "format",
        "source", "source_column", "update_cadence",
    ]
    REQUIRED_FRED = [
        "display_name", "description", "unit", "format",
        "source", "series_id", "update_cadence",
    ]

    def test_redfin_metrics_have_required_fields(self, model):
        for key, defn in model.redfin.items():
            for field in self.REQUIRED_REDFIN:
                assert field in defn, f"Redfin metric '{key}' missing field '{field}'"

    def test_fred_metrics_have_required_fields(self, model):
        for key, defn in model.fred.items():
            for field in self.REQUIRED_FRED:
                assert field in defn, f"FRED metric '{key}' missing field '{field}'"

    def test_format_values_are_valid(self, model):
        valid_formats = {"money", "number", "pct", "rate"}
        for key, defn in {**model.redfin, **model.fred}.items():
            assert defn["format"] in valid_formats, (
                f"Metric '{key}' has invalid format '{defn['format']}'"
            )

    def test_delta_color_values_are_valid(self, model):
        valid_colors = {"normal", "inverse"}
        for key, defn in {**model.redfin, **model.fred}.items():
            if "delta_color" in defn:
                assert defn["delta_color"] in valid_colors, (
                    f"Metric '{key}' has invalid delta_color '{defn['delta_color']}'"
                )


# ── Dashboard cards ──


class TestDashboardCards:
    def test_five_dashboard_cards(self, model):
        assert len(model.dashboard_cards()) == 5

    def test_ordered_by_position(self, model):
        positions = [c["dashboard_position"] for c in model.dashboard_cards()]
        assert positions == sorted(positions)

    def test_no_duplicate_positions(self, model):
        positions = [c["dashboard_position"] for c in model.dashboard_cards()]
        assert len(positions) == len(set(positions))

    def test_first_card_is_median_sale_price(self, model):
        assert model.dashboard_cards()[0]["key"] == "median_sale_price"

    def test_last_card_is_mortgage_rate(self, model):
        assert model.dashboard_cards()[-1]["key"] == "MORTGAGE30US"

    def test_all_cards_have_format(self, model):
        assert all("format" in c for c in model.dashboard_cards())

    def test_all_cards_have_delta_color(self, model):
        assert all("delta_color" in c for c in model.dashboard_cards())

    def test_all_cards_have_display_name(self, model):
        assert all("display_name" in c for c in model.dashboard_cards())


# ── Rankable metrics (chat engine allowlist) ──


class TestRankableMetrics:
    EXPECTED_RANKABLE = {
        "median_sale_price", "days_on_market", "inventory", "price_drop_pct",
        "homes_sold", "new_listings", "months_of_supply",
    }

    def test_seven_rankable_metrics(self, model):
        assert len(model.rankable_metric_names()) == 7

    def test_matches_expected_set(self, model):
        assert model.rankable_metric_names() == self.EXPECTED_RANKABLE

    def test_non_rankable_excluded(self, model):
        rankable = model.rankable_metric_names()
        assert "avg_sale_to_list" not in rankable
        assert "sold_above_list" not in rankable
        assert "price_mom" not in rankable

    def test_rankable_is_subset_of_primary(self, model):
        assert model.rankable_metric_names().issubset(
            set(model.primary_redfin_metrics().keys())
        )


# ── Grounding text (RAG + vector store) ──


class TestGroundingText:
    def test_contains_all_primary_redfin_metrics(self, model):
        text = model.grounding_text()
        for key in model.primary_redfin_metrics():
            assert key in text, f"Grounding text missing metric '{key}'"

    def test_contains_all_fred_series(self, model):
        text = model.grounding_text()
        for series_id in model.fred:
            assert series_id in text, f"Grounding text missing FRED series '{series_id}'"

    def test_contains_descriptions(self, model):
        text = model.grounding_text()
        assert "Median sale price" in text

    def test_contains_notes(self, model):
        text = model.grounding_text()
        assert "vector store" in text.lower()

    def test_not_empty(self, model):
        assert len(model.grounding_text()) > 500


# ── Ingestion mapping (pipeline) ──


class TestIngestionMapping:
    EXPECTED_RENAME = {
        "MEDIAN_SALE_PRICE": "median_sale_price",
        "MEDIAN_SALE_PRICE_MOM": "price_mom",
        "MEDIAN_SALE_PRICE_YOY": "price_yoy",
        "MEDIAN_DOM": "days_on_market",
        "INVENTORY": "inventory",
        "INVENTORY_MOM": "inventory_mom",
        "PRICE_DROPS": "price_drop_pct",
        "HOMES_SOLD": "homes_sold",
        "NEW_LISTINGS": "new_listings",
        "MONTHS_OF_SUPPLY": "months_of_supply",
        "AVG_SALE_TO_LIST": "avg_sale_to_list",
        "SOLD_ABOVE_LIST": "sold_above_list",
    }

    def test_rename_map_has_12_entries(self, model):
        assert len(model.redfin_column_rename_map()) == 12

    def test_rename_map_matches_expected(self, model):
        assert model.redfin_column_rename_map() == self.EXPECTED_RENAME

    def test_sqlite_columns_has_12_entries(self, model):
        assert len(model.redfin_sqlite_columns()) == 12

    def test_fred_series_ids(self, model):
        assert model.fred_series_ids() == ["MORTGAGE30US", "CPIAUCSL", "UNRATE", "HOUST"]

    def test_sqlite_table_creation(self, model):
        """Verify the generated CREATE TABLE works and has all columns."""
        import sqlite3
        conn = sqlite3.connect(":memory:")
        from pipeline.data_ingestion import _init_redfin_table
        _init_redfin_table(conn, "test_redfin")
        cursor = conn.execute("PRAGMA table_info(test_redfin)")
        col_names = {row[1] for row in cursor.fetchall()}
        conn.close()

        for col in self.EXPECTED_RENAME.values():
            assert col in col_names, f"Missing column '{col}' in generated table"
        # Structural columns
        for col in ["period_date", "metro_name", "state", "region_type", "loaded_at"]:
            assert col in col_names, f"Missing structural column '{col}'"


# ── Cross-module consistency ──


class TestCrossModuleConsistency:
    def test_pipeline_uses_semantic_model(self):
        from pipeline.data_ingestion import REDFIN_METRIC_RENAME, FRED_SERIES
        model = get_semantic_model()
        assert REDFIN_METRIC_RENAME == model.redfin_column_rename_map()
        assert FRED_SERIES == model.fred_series_ids()

    def test_vector_pipeline_uses_semantic_model(self):
        from pipeline.update_vectors import _doc_metric_definitions_text
        model = get_semantic_model()
        assert _doc_metric_definitions_text() == model.grounding_text()

    def test_api_card_config_matches_model(self):
        from backend.api import get_dashboard_card_config
        model = get_semantic_model()
        api_cards = get_dashboard_card_config()
        model_cards = model.dashboard_cards()
        assert len(api_cards) == len(model_cards)
        for api_card, model_card in zip(api_cards, model_cards):
            assert api_card["key"] == model_card["key"]
            assert api_card["display_name"] == model_card["display_name"]
