"""
Unified data ingestion module for HomeSignal.

Handles pulling, cleaning, and loading ALL data into SQLite:

  Redfin (7 geography-level TSV files from public S3):
    - Metro, City, County, State, Neighborhood, ZIP Code, National
    - Filtered: All Residential, non-seasonally-adjusted, last 18 months
    - Top-N regions per geography (configurable)
    - Table: redfin_metrics

  FRED (4 economic series via fredapi):
    - MORTGAGE30US  — 30-year fixed mortgage rate (weekly)
    - CPIAUCSL      — Consumer Price Index (monthly)
    - UNRATE        — Unemployment rate (monthly)
    - HOUST         — Housing starts (monthly, thousands of units)
    - Table: fred_metrics

Run:
  ./venv/bin/python pipeline/data_ingestion.py              # ingest all
  ./venv/bin/python pipeline/data_ingestion.py --redfin     # Redfin only
  ./venv/bin/python pipeline/data_ingestion.py --fred       # FRED only
  ./venv/bin/python pipeline/data_ingestion.py --download   # download Redfin files only
"""

from __future__ import annotations

import argparse
import os
import sqlite3
import urllib.request
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Callable, List, Optional, Tuple

import pandas as pd
from dotenv import load_dotenv
from fredapi import Fred

from backend.semantic_model import get_semantic_model


# ===========================================================================
# Configuration
# ===========================================================================

REDFIN_S3_BASE = (
    "https://redfin-public-data.s3.us-west-2.amazonaws.com"
    "/redfin_market_tracker"
)


@dataclass(frozen=True)
class RedfinSource:
    """One Redfin geography-level TSV file."""
    filename: str
    region_type: str
    top_n: Optional[int] = None  # None = keep all regions


REDFIN_SOURCES: List[RedfinSource] = [
    RedfinSource("redfin_metro_market_tracker.tsv000.gz",  "metro",        20),
    RedfinSource("city_market_tracker.tsv000.gz",          "city",         100),
    RedfinSource("county_market_tracker.tsv000.gz",        "county",       100),
    RedfinSource("state_market_tracker.tsv000.gz",         "state",        None),
    RedfinSource("neighborhood_market_tracker.tsv000.gz",  "neighborhood", 100),
    RedfinSource("zip_code_market_tracker.tsv000.gz",      "zip",          200),
    RedfinSource("us_national_market_tracker.tsv000.gz",   "national",     None),
]

_SM = get_semantic_model()
REDFIN_METRIC_RENAME = _SM.redfin_column_rename_map()
FRED_SERIES: List[str] = _SM.fred_series_ids()


@dataclass(frozen=True)
class Config:
    db_path: str = "data/homesignal.db"
    raw_dir: str = "data/raw"
    s3_base: str = REDFIN_S3_BASE

    # Redfin
    redfin_table: str = "redfin_metrics"
    redfin_property_type: str = "All Residential"

    # FRED
    fred_table: str = "fred_metrics"

    # Shared
    months_back: int = 18
    sqlite_chunksize: int = 2000


# ===========================================================================
# Shared helpers
# ===========================================================================

def _header(step: str, log: Callable[[str], None] = print) -> None:
    log(f"\n=== {step} ===")


def _ensure_dir(path: str) -> None:
    d = os.path.dirname(os.path.abspath(path))
    if d:
        os.makedirs(d, exist_ok=True)


# ===========================================================================
# REDFIN — Download
# ===========================================================================

def download_redfin(
    cfg: Config = Config(),
    log: Callable[[str], None] = print,
) -> Tuple[int, int]:
    """Download all Redfin TSV files from S3. Returns (succeeded, failed) counts."""
    os.makedirs(cfg.raw_dir, exist_ok=True)
    ok, fail = 0, 0
    for src in REDFIN_SOURCES:
        url = f"{cfg.s3_base}/{src.filename}"
        path = os.path.join(cfg.raw_dir, src.filename)
        log(f"Downloading {src.filename}...")
        try:
            urllib.request.urlretrieve(url, path)
            log(f"  OK: {path}")
            ok += 1
        except Exception as e:
            log(f"  FAILED: {e}")
            fail += 1
    log(f"Download complete: {ok} succeeded, {fail} failed")
    return ok, fail


# ===========================================================================
# REDFIN — Clean & Load
# ===========================================================================

def _load_tsv(path: str) -> pd.DataFrame:
    if not os.path.exists(path):
        raise FileNotFoundError(f"File not found: {path}")
    df = pd.read_csv(path, sep="\t", compression="gzip", low_memory=False)
    return df


def _clean_redfin(df: pd.DataFrame, cfg: Config) -> pd.DataFrame:
    """Filter to All Residential, non-seasonally-adjusted, last N months."""
    df["PROPERTY_TYPE"] = df["PROPERTY_TYPE"].astype(str).str.strip()

    required = ["PERIOD_BEGIN", "REGION"]
    present = [c for c in required if c in df.columns]
    df = df.dropna(subset=present).copy()

    df = df[df["PROPERTY_TYPE"] == cfg.redfin_property_type].copy()

    # Keep only non-seasonally-adjusted data to avoid duplicates
    if "IS_SEASONALLY_ADJUSTED" in df.columns:
        df = df[df["IS_SEASONALLY_ADJUSTED"] == False].copy()  # noqa: E712

    df["period_date_dt"] = pd.to_datetime(df["PERIOD_BEGIN"], errors="coerce")
    df = df.dropna(subset=["period_date_dt"]).copy()

    max_date = df["period_date_dt"].max()
    cutoff = max_date - pd.DateOffset(months=cfg.months_back)
    df = df[df["period_date_dt"] >= cutoff].copy()

    return df


def _top_n_regions(df: pd.DataFrame, top_n: Optional[int]) -> pd.DataFrame:
    """Keep only the top N regions by total HOMES_SOLD."""
    if top_n is None or "HOMES_SOLD" not in df.columns:
        return df

    group_keys = ["REGION"]
    if "STATE_CODE" in df.columns:
        group_keys.append("STATE_CODE")

    grouped = (
        df.groupby(group_keys, as_index=False)["HOMES_SOLD"]
        .sum()
        .sort_values("HOMES_SOLD", ascending=False)
    )
    top_keys = grouped.head(top_n)[group_keys]
    return df.merge(top_keys, on=group_keys, how="inner")


def _project_redfin(df: pd.DataFrame, region_type: str) -> pd.DataFrame:
    """Select, rename, and add region_type column."""
    df = df.copy()
    df["period_date"] = df["period_date_dt"].dt.strftime("%Y-%m-%d")
    df["metro_name"] = df["REGION"].astype(str).str.strip()
    df["state"] = (
        df["STATE_CODE"].astype(str).str.strip()
        if "STATE_CODE" in df.columns
        else ""
    )
    df["region_type"] = region_type

    available = {k: v for k, v in REDFIN_METRIC_RENAME.items() if k in df.columns}
    selected = df[
        ["period_date", "metro_name", "state", "region_type"]
        + list(available.keys())
    ].copy()
    selected = selected.rename(columns=available)

    for col in REDFIN_METRIC_RENAME.values():
        if col not in selected.columns:
            selected[col] = None
        else:
            selected[col] = pd.to_numeric(selected[col], errors="coerce")

    return selected


def _init_redfin_table(conn: sqlite3.Connection, table_name: str) -> None:
    """Drop and recreate the Redfin table for idempotent runs."""
    metric_cols = _SM.redfin_sqlite_columns()
    metric_col_defs = "\n".join(f"            {col} REAL," for col in metric_cols)

    conn.execute(f"DROP TABLE IF EXISTS {table_name}")
    conn.execute(f"""
        CREATE TABLE {table_name} (
            period_date       TEXT NOT NULL,
            metro_name        TEXT NOT NULL,
            state             TEXT NOT NULL,
            region_type       TEXT NOT NULL,

{metric_col_defs}

            loaded_at         TEXT NOT NULL
        )
    """)
    conn.execute(f"""
        CREATE INDEX IF NOT EXISTS idx_{table_name}_region
        ON {table_name} (region_type, metro_name, period_date)
    """)
    conn.commit()


def ingest_redfin(
    cfg: Config = Config(),
    log: Callable[[str], None] = print,
) -> int:
    """Clean and load all Redfin TSV files into SQLite. Returns total rows inserted."""
    _header("Redfin Ingestion", log)
    _ensure_dir(cfg.db_path)

    total_rows = 0
    skipped = []
    errors = []

    with sqlite3.connect(cfg.db_path) as conn:
        _init_redfin_table(conn, cfg.redfin_table)

        for src in REDFIN_SOURCES:
            path = os.path.join(cfg.raw_dir, src.filename)
            _header(f"Redfin {src.region_type}: {src.filename}", log)

            if not os.path.exists(path):
                log(f"  SKIP: not found at {path}")
                skipped.append(src.filename)
                continue

            try:
                df = _load_tsv(path)
                log(f"  Loaded {len(df):,} rows")

                df = _clean_redfin(df, cfg)
                log(f"  After cleaning: {len(df):,} rows")

                if df.empty:
                    log("  SKIP: no rows after cleaning")
                    skipped.append(src.filename)
                    continue

                df = _top_n_regions(df, src.top_n)
                df = _project_redfin(df, src.region_type)

                loaded_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
                df["loaded_at"] = loaded_at

                df.to_sql(
                    cfg.redfin_table,
                    con=conn,
                    if_exists="append",
                    index=False,
                    chunksize=cfg.sqlite_chunksize,
                )

                regions = df["metro_name"].nunique()
                log(f"  Inserted {len(df):,} rows | {regions} regions | "
                    f"{df['period_date'].min()} to {df['period_date'].max()}")
                total_rows += len(df)

            except Exception as e:
                log(f"  ERROR: {e}")
                errors.append(f"{src.filename}: {e}")

        conn.commit()

    log(f"\nRedfin summary: {total_rows:,} rows inserted, "
        f"{len(REDFIN_SOURCES) - len(skipped) - len(errors)}/{len(REDFIN_SOURCES)} sources")
    if skipped:
        log(f"  Skipped: {skipped}")
    if errors:
        log(f"  Errors: {errors}")

    return total_rows


# ===========================================================================
# FRED — Fetch, Clean & Load
# ===========================================================================

def _get_fred_client() -> Fred:
    key = os.getenv("FRED_API_KEY")
    if not key:
        raise RuntimeError("FRED_API_KEY not found in .env")
    return Fred(api_key=key)


def _fetch_fred_series(fred: Fred, series_id: str) -> Tuple[pd.Series, str]:
    """Fetch one FRED series and resolve its human-readable name."""
    series = fred.get_series(series_id)
    series_name = series_id
    try:
        info = fred.get_series_info(series_id)
        if isinstance(info, dict):
            series_name = info.get("title") or info.get("name") or series_id
    except Exception:
        pass
    return series, str(series_name)


def _clean_fred_series(series: pd.Series, months_back: int) -> pd.Series:
    """Filter to the last N months and drop NaN values."""
    today_ts = pd.Timestamp(datetime.now(timezone.utc).date())
    start_ts = today_ts - pd.DateOffset(months=months_back)

    idx = pd.to_datetime(series.index)
    mask = (idx >= start_ts) & (idx <= today_ts)
    filtered = series.loc[mask].dropna().copy()
    filtered.index = pd.to_datetime(filtered.index)
    return filtered


def _ensure_fred_table(conn: sqlite3.Connection, table_name: str) -> None:
    conn.execute(f"""
        CREATE TABLE IF NOT EXISTS {table_name} (
            series_id   TEXT NOT NULL,
            period_date TEXT NOT NULL,
            value       REAL,
            series_name TEXT,
            loaded_at   TEXT NOT NULL
        )
    """)
    conn.commit()


def ingest_fred(
    cfg: Config = Config(),
    log: Callable[[str], None] = print,
) -> int:
    """Fetch, clean, and load all FRED series into SQLite. Returns total rows inserted."""
    _header("FRED Ingestion", log)
    _ensure_dir(cfg.db_path)

    fred = _get_fred_client()
    total_rows = 0
    failed = []

    with sqlite3.connect(cfg.db_path) as conn:
        _ensure_fred_table(conn, cfg.fred_table)

        for series_id in FRED_SERIES:
            _header(f"FRED: {series_id}", log)
            try:
                series, series_name = _fetch_fred_series(fred, series_id)
                log(f"  Fetched {len(series):,} raw data points")

                filtered = _clean_fred_series(series, cfg.months_back)
                if filtered.empty:
                    log(f"  SKIP: no data in last {cfg.months_back} months")
                    failed.append(series_id)
                    continue

                log(f"  After cleaning: {len(filtered):,} data points "
                    f"({filtered.index.min().date()} to {filtered.index.max().date()})")

                out = pd.DataFrame({
                    "series_id": series_id,
                    "period_date": pd.to_datetime(filtered.index).strftime("%Y-%m-%d"),
                    "value": filtered.astype(float).values,
                    "series_name": series_name,
                })
                loaded_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
                out["loaded_at"] = loaded_at

                # Delete existing rows for this series+date range, then insert
                start_pd, end_pd = out["period_date"].min(), out["period_date"].max()
                cur = conn.cursor()
                cur.execute(
                    f"DELETE FROM {cfg.fred_table} "
                    "WHERE series_id = ? AND period_date >= ? AND period_date <= ?",
                    (series_id, start_pd, end_pd),
                )
                conn.commit()
                log(f"  Cleared {cur.rowcount:,} old rows for {series_id}")

                out.to_sql(
                    cfg.fred_table,
                    con=conn,
                    if_exists="append",
                    index=False,
                    chunksize=cfg.sqlite_chunksize,
                )
                conn.commit()

                log(f"  Inserted {len(out):,} rows | {series_name}")
                total_rows += len(out)

            except Exception as e:
                log(f"  ERROR: {series_id}: {e}")
                failed.append(series_id)

    succeeded = [s for s in FRED_SERIES if s not in failed]
    log(f"\nFRED summary: {total_rows:,} rows inserted | "
        f"Succeeded: {succeeded} | Failed: {failed}")
    return total_rows


# ===========================================================================
# Full pipeline
# ===========================================================================

# ===========================================================================
# CLI
# ===========================================================================

def main() -> None:
    parser = argparse.ArgumentParser(
        description="HomeSignal data ingestion — pull, clean, and load into SQLite"
    )
    parser.add_argument("--redfin", action="store_true", help="Ingest Redfin only")
    parser.add_argument("--fred", action="store_true", help="Ingest FRED only")
    parser.add_argument("--download", action="store_true", help="Download Redfin files only (no ingest)")
    parser.add_argument("--no-download", action="store_true", help="Skip Redfin download (use existing files)")
    args = parser.parse_args()

    load_dotenv()
    cfg = Config()

    if args.download:
        download_redfin(cfg)
        return

    # If neither --redfin nor --fred specified, run both
    run_redfin = args.redfin or (not args.redfin and not args.fred)
    run_fred = args.fred or (not args.redfin and not args.fred)

    if run_redfin:
        if not args.no_download:
            download_redfin(cfg)
        ingest_redfin(cfg)

    if run_fred:
        ingest_fred(cfg)

    print("\nDone.")


if __name__ == "__main__":
    main()
