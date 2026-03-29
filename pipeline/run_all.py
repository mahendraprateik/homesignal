"""
HomeSignal unified pipeline orchestrator.

Single entry point that runs the entire data pipeline in order:

  1. Freshness check (FRED + Redfin)
  2. Download Redfin raw files (if stale)
  3. Ingest FRED data → SQLite
  4. Ingest Redfin data → SQLite
  5. Rebuild structured vectors → ChromaDB (housing_market)
  6. Ingest web context → ChromaDB (housing_context)

Usage:
    python pipeline/run_all.py              # smart refresh (only stale sources)
    python pipeline/run_all.py --force      # force full rebuild
    python pipeline/run_all.py --skip-context  # skip web context ingestion
    python pipeline/run_all.py --context-only  # only run context ingestion
"""

from __future__ import annotations

import argparse
import sys
import time
from datetime import datetime, timezone
from typing import Callable

from dotenv import load_dotenv


def _log(msg: str) -> None:
    ts = datetime.now(timezone.utc).strftime("%H:%M:%S")
    print(f"[{ts}] {msg}")


def _run_step(
    name: str,
    fn: Callable[[], bool],
    errors: list,
) -> bool:
    """Run a pipeline step, log result, collect errors. Returns True on success."""
    _log(f"START: {name}")
    start = time.time()
    try:
        ok = fn()
        elapsed = time.time() - start
        if ok:
            _log(f"  DONE: {name} ({elapsed:.1f}s)")
        else:
            _log(f"  FAIL: {name} ({elapsed:.1f}s)")
            errors.append(name)
        return ok
    except Exception as e:
        elapsed = time.time() - start
        _log(f"  ERROR: {name}: {e} ({elapsed:.1f}s)")
        errors.append(f"{name}: {e}")
        return False


def run_pipeline(
    force: bool = False,
    skip_context: bool = False,
    context_only: bool = False,
) -> bool:
    """
    Run the full HomeSignal pipeline. Returns True if all steps succeeded.
    """
    load_dotenv()
    errors: list = []
    pipeline_start = time.time()

    _log("=" * 50)
    _log("HomeSignal Pipeline — Starting")
    _log("=" * 50)

    if context_only:
        _log("Mode: context-only")
        from pipeline.context_ingestion import main as context_main  # noqa: E402

        _run_step("Context ingestion", lambda: _call_void(context_main), errors)

        _print_summary(errors, pipeline_start)
        return len(errors) == 0

    # ------------------------------------------------------------------
    # Step 1: Check freshness
    # ------------------------------------------------------------------
    from pipeline.refresh import (
        Config as RefreshConfig,
        check_fred_freshness,
        check_redfin_freshness,
    )
    from pipeline.data_ingestion import (
        Config as IngestionConfig,
        download_redfin,
        ingest_fred,
        ingest_redfin,
    )

    cfg = RefreshConfig()
    fred_fresh = check_fred_freshness(cfg)
    redfin_fresh = check_redfin_freshness(cfg)

    _log(f"FRED:   {'STALE' if fred_fresh.is_stale else 'FRESH'} — {fred_fresh.reason}")
    _log(f"Redfin: {'STALE' if redfin_fresh.is_stale else 'FRESH'} — {redfin_fresh.reason}")

    run_fred = force or fred_fresh.is_stale
    run_redfin = force or redfin_fresh.is_stale
    data_updated = False

    ingestion_cfg = IngestionConfig()

    # ------------------------------------------------------------------
    # Step 2: Download Redfin raw files (if needed)
    # ------------------------------------------------------------------
    if run_redfin:
        _run_step(
            "Download Redfin files",
            lambda: download_redfin(ingestion_cfg, _log)[1] == 0,
            errors,
        )

    # ------------------------------------------------------------------
    # Step 3: Ingest FRED → SQLite
    # ------------------------------------------------------------------
    if run_fred:
        ok = _run_step("FRED ingest", lambda: ingest_fred(ingestion_cfg, _log) >= 0, errors)
        if ok:
            data_updated = True

    # ------------------------------------------------------------------
    # Step 4: Ingest Redfin → SQLite
    # ------------------------------------------------------------------
    if run_redfin:
        ok = _run_step("Redfin ingest", lambda: ingest_redfin(ingestion_cfg, _log) >= 0, errors)
        if ok:
            data_updated = True

    # ------------------------------------------------------------------
    # Step 5: Rebuild structured vectors → ChromaDB
    # ------------------------------------------------------------------
    if data_updated or force:
        from pipeline.update_vectors import main as vectors_main

        _run_step("Vector store rebuild", lambda: _call_void(vectors_main), errors)
    else:
        _log("SKIP: Vector store rebuild (no data changes)")

    # ------------------------------------------------------------------
    # Step 6: Context ingestion → ChromaDB
    # ------------------------------------------------------------------
    if not skip_context:
        from pipeline.context_ingestion import main as context_main  # noqa: E402

        _run_step("Context ingestion", lambda: _call_void(context_main), errors)
    else:
        _log("SKIP: Context ingestion (--skip-context)")

    # ------------------------------------------------------------------
    # Summary
    # ------------------------------------------------------------------
    _print_summary(errors, pipeline_start)
    return len(errors) == 0


def _call_void(fn: Callable) -> bool:
    """Call a void function, return True if no exception."""
    fn()
    return True


def _print_summary(errors: list, start_time: float) -> None:
    elapsed = time.time() - start_time
    _log("=" * 50)
    if errors:
        _log(f"Pipeline finished with {len(errors)} error(s) in {elapsed:.1f}s")
        for e in errors:
            _log(f"  - {e}")
    else:
        _log(f"Pipeline complete — all steps succeeded ({elapsed:.1f}s)")
    _log("=" * 50)


def main() -> None:
    parser = argparse.ArgumentParser(description="HomeSignal unified pipeline")
    parser.add_argument(
        "--force", action="store_true",
        help="Force full rebuild regardless of freshness",
    )
    parser.add_argument(
        "--skip-context", action="store_true",
        help="Skip web context ingestion step",
    )
    parser.add_argument(
        "--context-only", action="store_true",
        help="Only run context ingestion (skip data + vectors)",
    )
    args = parser.parse_args()

    ok = run_pipeline(
        force=args.force,
        skip_context=args.skip_context,
        context_only=args.context_only,
    )
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
