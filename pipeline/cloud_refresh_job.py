"""
Cloud refresh job entrypoint for scheduled updates.

Runs the HomeSignal pipeline, then publishes a versioned runtime snapshot to GCS.
This is intended for Cloud Scheduler -> Cloud Run Job workflows.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
import tarfile
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict

from dotenv import load_dotenv
from google.cloud import storage

# Ensure project root imports work when executed as a script.
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from pipeline.run_all import run_pipeline


def _utc_ts() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def _sha256(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def _create_snapshot_archive() -> str:
    data_dir = Path("data")
    db_path = data_dir / "homesignal.db"
    chroma_dir = data_dir / "chroma_db"

    if not db_path.exists():
        raise FileNotFoundError(f"Missing runtime database: {db_path}")
    if not chroma_dir.exists():
        raise FileNotFoundError(f"Missing runtime vector store: {chroma_dir}")

    tmp = tempfile.NamedTemporaryFile(prefix="homesignal_snapshot_", suffix=".tar.gz", delete=False)
    tmp_path = tmp.name
    tmp.close()

    with tarfile.open(tmp_path, "w:gz") as tar:
        tar.add(str(db_path), arcname="data/homesignal.db")
        tar.add(str(chroma_dir), arcname="data/chroma_db")
    return tmp_path


def _upload_snapshot(
    bucket_name: str,
    prefix: str,
    archive_path: str,
    dry_run: bool = False,
) -> Dict[str, str]:
    ts = _utc_ts()
    prefix_clean = prefix.strip("/ ")
    snapshots_prefix = f"{prefix_clean}/snapshots" if prefix_clean else "snapshots"
    snapshot_blob_name = f"{snapshots_prefix}/homesignal_snapshot_{ts}.tar.gz"
    manifest_blob_name = f"{prefix_clean}/latest.json" if prefix_clean else "latest.json"

    checksum = _sha256(archive_path)
    archive_size = str(os.path.getsize(archive_path))

    manifest = {
        "snapshot_blob": snapshot_blob_name,
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "sha256": checksum,
        "bytes": archive_size,
    }

    if dry_run:
        print("[dry-run] Would upload snapshot:", snapshot_blob_name)
        print("[dry-run] Would update manifest:", manifest_blob_name)
        return {
            "snapshot_blob": snapshot_blob_name,
            "manifest_blob": manifest_blob_name,
        }

    client = storage.Client()
    bucket = client.bucket(bucket_name)

    snapshot_blob = bucket.blob(snapshot_blob_name)
    snapshot_blob.upload_from_filename(archive_path)

    manifest_blob = bucket.blob(manifest_blob_name)
    manifest_blob.upload_from_string(
        json.dumps(manifest, indent=2, sort_keys=True),
        content_type="application/json",
    )

    return {
        "snapshot_blob": snapshot_blob_name,
        "manifest_blob": manifest_blob_name,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="HomeSignal cloud refresh job")
    parser.add_argument("--force", action="store_true", help="Force full refresh")
    parser.add_argument("--skip-context", action="store_true", help="Skip context ingestion")
    parser.add_argument("--context-only", action="store_true", help="Run context ingestion only")
    parser.add_argument("--dry-run", action="store_true", help="Print actions without uploading")
    args = parser.parse_args()

    load_dotenv()
    bucket = os.getenv("HOMESIGNAL_GCS_BUCKET", "").strip()
    prefix = os.getenv("HOMESIGNAL_GCS_PREFIX", "homesignal").strip()
    if not bucket:
        raise RuntimeError("HOMESIGNAL_GCS_BUCKET is required for cloud refresh job")

    print("Starting pipeline run...")
    ok = run_pipeline(
        force=args.force,
        skip_context=args.skip_context,
        context_only=args.context_only,
    )
    if not ok:
        raise RuntimeError("Pipeline failed; snapshot upload aborted")

    print("Creating runtime snapshot archive...")
    archive_path = _create_snapshot_archive()
    try:
        result = _upload_snapshot(
            bucket_name=bucket,
            prefix=prefix,
            archive_path=archive_path,
            dry_run=args.dry_run,
        )
    finally:
        try:
            os.remove(archive_path)
        except OSError:
            pass

    print("Snapshot published.")
    print("snapshot_blob:", result["snapshot_blob"])
    print("manifest_blob:", result["manifest_blob"])


if __name__ == "__main__":
    main()
