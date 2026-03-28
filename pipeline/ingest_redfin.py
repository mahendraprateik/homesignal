"""
Ingest Redfin Metro Market Tracker data into SQLite.

Reads the Redfin TSV.GZ file from `data/raw/`, filters to:
- PROPERTY_TYPE == 'All Residential'
- REGION_TYPE == 'metro'
- Top 20 metros by total HOMES_SOLD (over the last 18 months)
- Only the last 18 months of data (relative to max PERIOD_BEGIN in the filtered dataset)

Stores into SQLite at `data/homesignal.db` into table `redfin_metrics` with:
- period_date (from PERIOD_BEGIN)
- metro_name (from REGION)
- state (from STATE_CODE)
- selected metric columns
- loaded_at timestamp

Run:
  ./venv/bin/python pipeline/ingest_redfin.py
"""

from __future__ import annotations

import os
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone

import pandas as pd
from dotenv import load_dotenv


@dataclass(frozen=True)
class Config:
    redfin_path: str = "data/raw/redfin_metro_market_tracker.tsv000.gz"
    db_path: str = "data/homesignal.db"
    table_name: str = "redfin_metrics"

    # Filters per prompt
    property_type: str = "All Residential"
    region_type: str = "metro"

    # Top-N selection
    top_n_metros: int = 20

    # Time window selection
    months_back: int = 18

    # Chunk size for faster SQLite inserts
    sqlite_chunksize: int = 2000


def _print_header(step: str) -> None:
    print(f"\n=== {step} ===")


def _ensure_parent_dir(path: str) -> None:
    parent = os.path.dirname(os.path.abspath(path))
    if parent and not os.path.exists(parent):
        os.makedirs(parent, exist_ok=True)


def _load_redfin_tsv(path: str) -> pd.DataFrame:
    if not os.path.exists(path):
        raise FileNotFoundError(
            f"Redfin file not found: {path}\n"
            "Expected it at `data/raw/redfin_metro_market_tracker.tsv000.gz`."
        )

    _print_header("Loading Redfin TSV.GZ")
    print(f"Reading: {path}")

    # PERIOD_BEGIN looks like 'YYYY-MM-DD' in your sample.
    # We keep it as string initially and convert after filtering for safety/perf.
    df = pd.read_csv(
        path,
        sep="\t",
        compression="gzip",
        low_memory=False,
    )

    print(f"Loaded rows: {len(df):,}")
    print(f"Loaded columns: {len(df.columns)}")
    return df


def _apply_redfin_filters(df: pd.DataFrame, cfg: Config) -> pd.DataFrame:
    _print_header("Filtering Redfin data")

    # Normalize string filters (protect against accidental whitespace)
    df["PROPERTY_TYPE"] = df["PROPERTY_TYPE"].astype(str).str.strip()
    df["REGION_TYPE"] = df["REGION_TYPE"].astype(str).str.strip()

    # Drop obviously incomplete rows early
    df = df.dropna(subset=["PERIOD_BEGIN", "REGION", "STATE_CODE", "HOMES_SOLD"]).copy()

    # Apply requested filters
    df = df[df["PROPERTY_TYPE"] == cfg.property_type].copy()
    print(f"After PROPERTY_TYPE filter: {len(df):,} rows")

    df = df[df["REGION_TYPE"] == cfg.region_type].copy()
    print(f"After REGION_TYPE filter: {len(df):,} rows")

    # Convert PERIOD_BEGIN to datetime for last-18-month windowing
    df["period_date_dt"] = pd.to_datetime(df["PERIOD_BEGIN"], errors="coerce", utc=False)
    df = df.dropna(subset=["period_date_dt"]).copy()

    # Last 18 months relative to max PERIOD_BEGIN in this filtered set
    max_date = df["period_date_dt"].max()
    cutoff = max_date - pd.DateOffset(months=cfg.months_back)

    print(f"Max PERIOD_BEGIN in filtered data: {max_date.date()}")
    print(f"Cutoff for last {cfg.months_back} months: {cutoff.date()}")

    df = df[df["period_date_dt"] >= cutoff].copy()
    print(f"After last-18-month filter: {len(df):,} rows")

    return df


def _keep_top_metros_by_homes_sold(df: pd.DataFrame, cfg: Config) -> pd.DataFrame:
    _print_header(f"Keeping top {cfg.top_n_metros} metros by HOMES_SOLD")

    # Define metro identity as (metro_name, state) because the dataset also includes state codes.
    df["metro_name"] = df["REGION"].astype(str).str.strip()
    df["state"] = df["STATE_CODE"].astype(str).str.strip()

    grouped = (
        df.groupby(["metro_name", "state"], as_index=False)["HOMES_SOLD"]
        .sum()
        .rename(columns={"HOMES_SOLD": "homes_sold_total"})
    )

    grouped = grouped.sort_values("homes_sold_total", ascending=False)

    top_keys = grouped.head(cfg.top_n_metros)[["metro_name", "state"]]
    top_count = len(top_keys)

    print(f"Computed metro totals for: {len(grouped):,} unique (metro,state) keys")
    print(f"Top selected metros: {top_count}")

    # Keep only rows matching the selected top keys
    df = df.merge(top_keys, on=["metro_name", "state"], how="inner")

    return df


def _project_columns_and_rename(df: pd.DataFrame) -> pd.DataFrame:
    _print_header("Projecting required columns")

    # Map requested schema to TSV columns
    rename_map = {
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

    # period_date requested from PERIOD_BEGIN
    df["period_date"] = df["period_date_dt"].dt.strftime("%Y-%m-%d")

    # Prepare selected columns in the exact order requested
    selected = df[
        [
            "period_date",
            "metro_name",
            "state",
            *rename_map.keys(),
        ]
    ].copy()

    selected = selected.rename(columns=rename_map)

    # Ensure numeric columns remain numeric (SQLite will store as REAL; NaNs become NULL)
    numeric_cols = [
        "median_sale_price",
        "price_mom",
        "price_yoy",
        "days_on_market",
        "inventory",
        "inventory_mom",
        "price_drop_pct",
        "homes_sold",
        "new_listings",
        "months_of_supply",
        "avg_sale_to_list",
        "sold_above_list",
    ]
    for c in numeric_cols:
        selected[c] = pd.to_numeric(selected[c], errors="coerce")

    return selected


def _ensure_table(conn: sqlite3.Connection, table_name: str) -> None:
    # Using a simple schema with REAL/TEXT. Primary key is intentionally omitted
    # since duplicates/idempotency rules were not specified in the prompt.
    _print_header("Ensuring SQLite table exists")

    conn.execute(
        f"""
        CREATE TABLE IF NOT EXISTS {table_name} (
            period_date TEXT NOT NULL,
            metro_name TEXT NOT NULL,
            state TEXT NOT NULL,

            median_sale_price REAL,
            price_mom REAL,
            price_yoy REAL,

            days_on_market REAL,

            inventory REAL,
            inventory_mom REAL,

            price_drop_pct REAL,

            homes_sold REAL,
            new_listings REAL,

            months_of_supply REAL,

            avg_sale_to_list REAL,
            sold_above_list REAL,

            loaded_at TEXT NOT NULL
        )
        """
    )
    conn.commit()


def _write_to_sqlite(df: pd.DataFrame, cfg: Config) -> None:
    _print_header("Writing to SQLite")

    _ensure_parent_dir(cfg.db_path)

    # Load/insert
    loaded_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
    df = df.copy()
    df["loaded_at"] = loaded_at

    with sqlite3.connect(cfg.db_path) as conn:
        _ensure_table(conn, cfg.table_name)

        # Append to preserve loaded_at history across runs
        # (If you want replace behavior instead, change if_exists to 'replace'.)
        df.to_sql(
            cfg.table_name,
            con=conn,
            if_exists="append",
            index=False,
            chunksize=cfg.sqlite_chunksize,
        )
        conn.commit()

    print(f"Inserted rows: {len(df):,}")
    print(f"loaded_at: {loaded_at}")


def main() -> None:
    load_dotenv()  # Requirement: use python-dotenv

    cfg = Config()

    try:
        print("\nStarting Redfin ingestion...")

        df = _load_redfin_tsv(cfg.redfin_path)
        df = _apply_redfin_filters(df, cfg)
        df = _keep_top_metros_by_homes_sold(df, cfg)

        df = _project_columns_and_rename(df)

        # Print summary required by prompt
        metros_loaded = df["metro_name"].nunique()
        date_min = df["period_date"].min()
        date_max = df["period_date"].max()
        row_count = len(df)

        print("\n--- Summary (pre-write) ---")
        print(f"Metros loaded: {metros_loaded}")
        print(f"Date range: {date_min} to {date_max}")
        print(f"Row count: {row_count:,}")

        _write_to_sqlite(df, cfg)

        print("\nIngestion complete successfully.")

    except FileNotFoundError as e:
        print(f"FAIL: {e}")
        raise
    except Exception as e:
        # Keep error message visible for debugging during take-home interview runs.
        print(f"FAIL: Unexpected error: {e}")
        raise


if __name__ == "__main__":
    main()