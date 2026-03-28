"""
Ingest FRED economic data into SQLite.

Series ingested:
  MORTGAGE30US  - 30-year fixed mortgage rate (weekly)
  CPIAUCSL      - Consumer Price Index, all urban consumers (monthly)
  UNRATE        - Unemployment rate (monthly)
  HOUST         - Housing starts, total (monthly, thousands of units)

Requirements:
1) Connect to FRED API using fredapi library
2) Pull last 18 months of data (relative to today) for each series
3) Store in SQLite at data/homesignal.db, table: fred_metrics
4) Idempotent: delete-then-reinsert for each series+date range

Run:
  ./venv/bin/python pipeline/ingest_fred.py
"""

from __future__ import annotations

import os
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import List, Tuple

import pandas as pd
from dotenv import load_dotenv
from fredapi import Fred


@dataclass(frozen=True)
class Config:
    db_path: str = "data/homesignal.db"
    table_name: str = "fred_metrics"
    months_back: int = 18
    sqlite_chunksize: int = 2000


# All FRED series to ingest. Add new series here — no other code changes needed.
SERIES_TO_INGEST: List[str] = [
    "MORTGAGE30US",   # 30-year fixed mortgage rate (weekly)
    "CPIAUCSL",       # Consumer Price Index - All Urban Consumers (monthly)
    "UNRATE",         # Unemployment Rate (monthly)
    "HOUST",          # Housing Starts: Total (monthly, thousands of units)
]


def _print_header(step: str) -> None:
    print(f"\n=== {step} ===")


def _ensure_parent_dir(path: str) -> None:
    parent = os.path.dirname(os.path.abspath(path))
    if parent and not os.path.exists(parent):
        os.makedirs(parent, exist_ok=True)


def _get_fred_client() -> Fred:
    fred_key = os.getenv("FRED_API_KEY")
    if not fred_key:
        raise RuntimeError("FAIL: FRED_API_KEY not found in .env")
    return Fred(api_key=fred_key)


def _fetch_series(fred: Fred, series_id: str) -> Tuple[pd.Series, str]:
    _print_header(f"Fetching FRED series: {series_id}")
    series = fred.get_series(series_id)

    series_name = series_id
    try:
        info = fred.get_series_info(series_id)
        if isinstance(info, dict):
            series_name = info.get("title") or info.get("name") or series_id
    except Exception as e:
        print(f"WARN: Could not resolve series_name for {series_id}: {e}")

    print(f"  Fetched {len(series):,} raw rows for {series_id}")
    return series, str(series_name)


def _filter_last_n_months(series: pd.Series, months_back: int) -> pd.Series:
    today_ts = pd.Timestamp(datetime.now(timezone.utc).date())
    start_ts = today_ts - pd.DateOffset(months=months_back)

    idx = pd.to_datetime(series.index)
    mask = (idx >= start_ts) & (idx <= today_ts)
    filtered = series.loc[mask].dropna().copy()
    filtered.index = pd.to_datetime(filtered.index)

    if len(filtered) == 0:
        raise RuntimeError(
            f"No data returned for series after filtering. "
            f"start={start_ts.date()} end={today_ts.date()}"
        )

    print(f"  Filtered range: {start_ts.date()} to {today_ts.date()} — {len(filtered):,} rows")
    return filtered


def _ensure_table(conn: sqlite3.Connection, table_name: str) -> None:
    conn.execute(
        f"""
        CREATE TABLE IF NOT EXISTS {table_name} (
            series_id TEXT NOT NULL,
            period_date TEXT NOT NULL,
            value REAL,
            series_name TEXT,
            loaded_at TEXT NOT NULL
        )
        """
    )
    conn.commit()


def _delete_date_range(
    conn: sqlite3.Connection,
    table_name: str,
    series_id: str,
    start_pd: str,
    end_pd: str,
) -> int:
    cur = conn.cursor()
    cur.execute(
        f"""
        DELETE FROM {table_name}
        WHERE series_id = ?
          AND period_date >= ?
          AND period_date <= ?
        """,
        (series_id, start_pd, end_pd),
    )
    conn.commit()
    return cur.rowcount


def _ingest_series(cfg: Config, fred: Fred, series_id: str) -> None:
    """Fetch, filter, and write one FRED series to SQLite."""
    series, series_name = _fetch_series(fred, series_id)
    filtered = _filter_last_n_months(series, cfg.months_back)

    out = pd.DataFrame(
        {
            "series_id": series_id,
            "period_date": pd.to_datetime(filtered.index).strftime("%Y-%m-%d"),
            "value": filtered.astype(float).values,
            "series_name": series_name,
        }
    )

    loaded_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
    out["loaded_at"] = loaded_at

    _ensure_parent_dir(cfg.db_path)

    with sqlite3.connect(cfg.db_path) as conn:
        _ensure_table(conn, cfg.table_name)

        start_pd = out["period_date"].min()
        end_pd = out["period_date"].max()
        deleted = _delete_date_range(conn, cfg.table_name, series_id, start_pd, end_pd)
        print(f"  Deleted {deleted:,} existing rows for {series_id} ({start_pd} to {end_pd})")

        out.to_sql(
            cfg.table_name,
            con=conn,
            if_exists="append",
            index=False,
            chunksize=cfg.sqlite_chunksize,
        )
        conn.commit()

    print(f"  Inserted {len(out):,} rows | series_name: {series_name} | loaded_at: {loaded_at}")


def main() -> None:
    load_dotenv()
    cfg = Config()

    print("\nStarting FRED ingestion...")
    print(f"Series to ingest: {SERIES_TO_INGEST}")

    fred = _get_fred_client()

    failed: List[str] = []
    for series_id in SERIES_TO_INGEST:
        try:
            _ingest_series(cfg, fred, series_id)
        except Exception as e:
            print(f"WARN: Failed to ingest {series_id}: {e}")
            failed.append(series_id)

    _print_header("Summary")
    succeeded = [s for s in SERIES_TO_INGEST if s not in failed]
    print(f"Succeeded: {succeeded}")
    if failed:
        print(f"Failed: {failed}")
    print("\nFRED ingestion complete.")


if __name__ == "__main__":
    main()
