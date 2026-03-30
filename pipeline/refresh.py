"""
Auto-refresh pipeline for HomeSignal.

Checks freshness for both data sources and only runs ingestion when data is stale.
Uses pipeline.data_ingestion for all pull/clean/load operations.

Freshness rules:
  FRED:   Stale if MAX(period_date) for MORTGAGE30US is > 8 days ago
          (FRED publishes weekly on Thursdays; 8-day buffer handles delays)
  Redfin: Stale if MAX(period_date) in redfin_metrics is > 35 days ago
          (Redfin publishes monthly; 35-day buffer handles publication lag)
          Also stale if no raw TSV files exist locally.
"""
from __future__ import annotations

import os
import sqlite3
import subprocess
import sys
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Callable, Optional

from dotenv import load_dotenv

try:
    from pipeline.data_ingestion import (
        Config as IngestionConfig,
        download_redfin,
        ingest_redfin,
        ingest_fred,
    )
except ModuleNotFoundError:
    from data_ingestion import (  # type: ignore[no-redef]
        Config as IngestionConfig,
        download_redfin,
        ingest_redfin,
        ingest_fred,
    )


@dataclass(frozen=True)
class Config:
    db_path: str = "data/homesignal.db"
    redfin_raw_dir: str = "data/raw"
    fred_stale_days: int = 8      # FRED updates weekly; flag stale after 8 days
    redfin_stale_days: int = 35   # Redfin updates monthly; flag stale after 35 days


@dataclass
class FreshnessResult:
    source: str                   # "fred" or "redfin"
    is_stale: bool
    latest_period: Optional[str]  # ISO date string of newest data point, or None
    loaded_at: Optional[str]      # ISO timestamp of last pipeline run, or None
    reason: str                   # Human-readable explanation


@dataclass
class RefreshResult:
    fred_freshness: FreshnessResult
    redfin_freshness: FreshnessResult
    fred_updated: bool = False
    redfin_updated: bool = False
    vectors_rebuilt: bool = False
    errors: list = field(default_factory=list)

    @property
    def any_updated(self) -> bool:
        return self.fred_updated or self.redfin_updated


def check_fred_freshness(cfg: Config) -> FreshnessResult:
    if not os.path.exists(cfg.db_path):
        return FreshnessResult(
            source="fred",
            is_stale=True,
            latest_period=None,
            loaded_at=None,
            reason="No FRED data in database",
        )
    try:
        with sqlite3.connect(cfg.db_path) as conn:
            cur = conn.cursor()
            cur.execute(
                "SELECT MAX(period_date), MAX(loaded_at) FROM fred_metrics"
                " WHERE series_id = 'MORTGAGE30US'"
            )
            row = cur.fetchone()
    except sqlite3.OperationalError:
        return FreshnessResult(
            source="fred",
            is_stale=True,
            latest_period=None,
            loaded_at=None,
            reason="No FRED data in database",
        )
    except Exception as e:
        return FreshnessResult(
            source="fred",
            is_stale=True,
            latest_period=None,
            loaded_at=None,
            reason=f"Database error: {e}",
        )

    if row is None or row[0] is None:
        return FreshnessResult(
            source="fred",
            is_stale=True,
            latest_period=None,
            loaded_at=None,
            reason="No FRED data in database",
        )

    latest_period_str = str(row[0])
    loaded_at_str = str(row[1]) if row[1] else None

    try:
        latest_period = datetime.strptime(latest_period_str[:10], "%Y-%m-%d").date()
    except ValueError:
        return FreshnessResult(
            source="fred",
            is_stale=True,
            latest_period=latest_period_str,
            loaded_at=loaded_at_str,
            reason=f"Could not parse FRED date: {latest_period_str}",
        )

    today = datetime.now(timezone.utc).date()
    days_old = (today - latest_period).days

    if days_old > cfg.fred_stale_days:
        return FreshnessResult(
            source="fred",
            is_stale=True,
            latest_period=latest_period_str,
            loaded_at=loaded_at_str,
            reason=f"Last FRED data: {latest_period} ({days_old} days ago)",
        )

    return FreshnessResult(
        source="fred",
        is_stale=False,
        latest_period=latest_period_str,
        loaded_at=loaded_at_str,
        reason=f"FRED current through {latest_period}",
    )


def check_redfin_freshness(cfg: Config) -> FreshnessResult:
    if not os.path.exists(cfg.db_path):
        return FreshnessResult(
            source="redfin",
            is_stale=True,
            latest_period=None,
            loaded_at=None,
            reason="No Redfin data in database",
        )

    try:
        with sqlite3.connect(cfg.db_path) as conn:
            cur = conn.cursor()
            cur.execute("SELECT MAX(period_date), MAX(loaded_at) FROM redfin_metrics")
            row = cur.fetchone()
    except sqlite3.OperationalError:
        return FreshnessResult(
            source="redfin",
            is_stale=True,
            latest_period=None,
            loaded_at=None,
            reason="No Redfin data in database",
        )
    except Exception as e:
        return FreshnessResult(
            source="redfin",
            is_stale=True,
            latest_period=None,
            loaded_at=None,
            reason=f"Database error: {e}",
        )

    if row is None or row[0] is None:
        return FreshnessResult(
            source="redfin",
            is_stale=True,
            latest_period=None,
            loaded_at=None,
            reason="No Redfin data in database",
        )

    latest_period_str = str(row[0])
    loaded_at_str = str(row[1]) if row[1] else None

    try:
        latest_period = datetime.strptime(latest_period_str[:10], "%Y-%m-%d").date()
    except ValueError:
        return FreshnessResult(
            source="redfin",
            is_stale=True,
            latest_period=latest_period_str,
            loaded_at=loaded_at_str,
            reason=f"Could not parse Redfin date: {latest_period_str}",
        )

    today = datetime.now(timezone.utc).date()
    days_old = (today - latest_period).days

    if days_old > cfg.redfin_stale_days:
        has_files = os.path.isdir(cfg.redfin_raw_dir) and any(
            f.endswith(".gz") for f in os.listdir(cfg.redfin_raw_dir)
        )
        extra = " and no local TSV files present" if not has_files else ""
        return FreshnessResult(
            source="redfin",
            is_stale=True,
            latest_period=latest_period_str,
            loaded_at=loaded_at_str,
            reason=f"Last Redfin data: {latest_period} ({days_old} days ago){extra}",
        )

    return FreshnessResult(
        source="redfin",
        is_stale=False,
        latest_period=latest_period_str,
        loaded_at=loaded_at_str,
        reason=f"Redfin current through {latest_period}",
    )


def _run_script(script_path: str, log: Callable[[str], None] = print) -> bool:
    proc = subprocess.Popen(
        [sys.executable, script_path],
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
    )
    for line in proc.stdout:
        log(line.rstrip())
    proc.wait()
    return proc.returncode == 0


def run_refresh(
    cfg: Optional[Config] = None,
    force: bool = False,
    log: Optional[Callable[[str], None]] = None,
) -> RefreshResult:
    if cfg is None:
        cfg = Config()
    if log is None:
        log = print

    load_dotenv()

    fred_freshness = check_fred_freshness(cfg)
    redfin_freshness = check_redfin_freshness(cfg)

    result = RefreshResult(
        fred_freshness=fred_freshness,
        redfin_freshness=redfin_freshness,
    )

    log(f"FRED:   {'STALE' if fred_freshness.is_stale else 'FRESH'} — {fred_freshness.reason}")
    log(f"Redfin: {'STALE' if redfin_freshness.is_stale else 'FRESH'} — {redfin_freshness.reason}")

    ingestion_cfg = IngestionConfig()

    if fred_freshness.is_stale or force:
        log("Running FRED ingest...")
        try:
            ingest_fred(ingestion_cfg, log)
            result.fred_updated = True
        except Exception as e:
            log(f"FRED ingest error: {e}")
            result.errors.append(f"FRED ingest failed: {e}")

    if redfin_freshness.is_stale or force:
        log("Downloading Redfin files...")
        ok, fail = download_redfin(ingestion_cfg, log)
        if fail > 0:
            result.errors.append(f"Redfin download: {fail} files failed")

        log("Running Redfin ingest...")
        try:
            ingest_redfin(ingestion_cfg, log)
            result.redfin_updated = True
        except Exception as e:
            log(f"Redfin ingest error: {e}")
            result.errors.append(f"Redfin ingest failed: {e}")

    if result.any_updated:
        log("Rebuilding vector store...")
        pipeline_dir = os.path.dirname(os.path.abspath(__file__))
        script = os.path.join(pipeline_dir, "update_vectors.py")
        if _run_script(script, log):
            result.vectors_rebuilt = True
        else:
            result.errors.append("Vector rebuild failed")

    return result


def main() -> None:
    import argparse
    parser = argparse.ArgumentParser(description="HomeSignal data refresh")
    parser.add_argument("--force", action="store_true", help="Force refresh even if data appears current")
    parser.add_argument("--check-only", action="store_true", help="Only check freshness, do not refresh")
    args = parser.parse_args()

    load_dotenv()
    cfg = Config()

    if args.check_only:
        fred = check_fred_freshness(cfg)
        redfin = check_redfin_freshness(cfg)
        print(f"FRED:   {'STALE' if fred.is_stale else 'FRESH'} — {fred.reason}")
        print(f"Redfin: {'STALE' if redfin.is_stale else 'FRESH'} — {redfin.reason}")
        return

    result = run_refresh(cfg=cfg, force=args.force)
    print("\n=== Refresh Summary ===")
    print(f"FRED updated:     {result.fred_updated}")
    print(f"Redfin updated:   {result.redfin_updated}")
    print(f"Vectors rebuilt:  {result.vectors_rebuilt}")
    if result.errors:
        print(f"Errors: {result.errors}")

if __name__ == "__main__":
    main()
