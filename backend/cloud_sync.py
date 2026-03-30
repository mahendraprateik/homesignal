"""
Cloud snapshot sync utilities for HomeSignal runtime data.

This module lets the app pull the latest prebuilt data snapshot from GCS:
  - data/homesignal.db
  - data/chroma_db/

The refresh job publishes a manifest (latest.json) and a tar.gz snapshot.
The app can periodically check and apply updates safely.
"""

from __future__ import annotations

import json
import os
import shutil
import tarfile
import tempfile
import threading
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Dict

from google.cloud import storage


_SYNC_LOCK = threading.Lock()


@dataclass(frozen=True)
class CloudSyncConfig:
    bucket: str = os.getenv("HOMESIGNAL_GCS_BUCKET", "").strip()
    prefix: str = os.getenv("HOMESIGNAL_GCS_PREFIX", "homesignal").strip()
    data_dir: str = "data"
    marker_file: str = "data/.cloud_snapshot_marker.json"

    @property
    def manifest_blob_name(self) -> str:
        prefix = self.prefix.rstrip("/")
        return f"{prefix}/latest.json" if prefix else "latest.json"

    @property
    def enabled(self) -> bool:
        return bool(self.bucket)


def _read_local_marker(path: str) -> Dict[str, Any]:
    if not os.path.exists(path):
        return {}
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def _write_local_marker(path: str, payload: Dict[str, Any]) -> None:
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(payload, f, indent=2, sort_keys=True)


def _load_remote_manifest(cfg: CloudSyncConfig) -> Dict[str, Any]:
    client = storage.Client()
    blob = client.bucket(cfg.bucket).blob(cfg.manifest_blob_name)
    raw = blob.download_as_text()
    manifest = json.loads(raw)
    if not manifest.get("snapshot_blob"):
        raise RuntimeError("Remote manifest missing 'snapshot_blob'")
    return manifest


def _apply_snapshot(archive_path: str, data_dir: str) -> None:
    with tempfile.TemporaryDirectory(prefix="homesignal_sync_extract_") as td:
        with tarfile.open(archive_path, "r:gz") as tar:
            tar.extractall(td, filter="data")

        extracted_data = os.path.join(td, "data")
        src_db = os.path.join(extracted_data, "homesignal.db")
        src_chroma = os.path.join(extracted_data, "chroma_db")
        if not os.path.exists(src_db):
            raise RuntimeError("Snapshot missing data/homesignal.db")
        if not os.path.isdir(src_chroma):
            raise RuntimeError("Snapshot missing data/chroma_db/")

        os.makedirs(data_dir, exist_ok=True)

        db_target = os.path.join(data_dir, "homesignal.db")
        chroma_target = os.path.join(data_dir, "chroma_db")

        db_tmp = f"{db_target}.tmp"
        shutil.copy2(src_db, db_tmp)
        os.replace(db_tmp, db_target)

        chroma_tmp = os.path.join(data_dir, ".chroma_db_tmp")
        if os.path.exists(chroma_tmp):
            shutil.rmtree(chroma_tmp, ignore_errors=True)
        shutil.copytree(src_chroma, chroma_tmp)

        if os.path.exists(chroma_target):
            shutil.rmtree(chroma_target, ignore_errors=True)
        os.replace(chroma_tmp, chroma_target)


def sync_cloud_snapshot_if_needed(force: bool = False) -> Dict[str, Any]:
    """
    Sync latest snapshot from GCS if manifest points to a newer version.

    Returns:
        {
            "enabled": bool,
            "updated": bool,
            "snapshot_blob": str | None,
            "reason": str
        }
    """
    cfg = CloudSyncConfig()
    if not cfg.enabled:
        return {
            "enabled": False,
            "updated": False,
            "snapshot_blob": None,
            "reason": "HOMESIGNAL_GCS_BUCKET not configured",
        }

    with _SYNC_LOCK:
        remote = _load_remote_manifest(cfg)
        remote_blob = str(remote["snapshot_blob"])

        local_marker = _read_local_marker(cfg.marker_file)
        local_blob = str(local_marker.get("snapshot_blob", ""))
        if not force and local_blob == remote_blob:
            return {
                "enabled": True,
                "updated": False,
                "snapshot_blob": remote_blob,
                "reason": "Already on latest snapshot",
            }

        client = storage.Client()
        bucket = client.bucket(cfg.bucket)
        blob = bucket.blob(remote_blob)
        if not blob.exists():
            raise RuntimeError(f"Snapshot blob not found: gs://{cfg.bucket}/{remote_blob}")

        with tempfile.NamedTemporaryFile(prefix="homesignal_snapshot_", suffix=".tar.gz", delete=False) as tmp:
            tmp_path = tmp.name
        try:
            blob.download_to_filename(tmp_path)
            _apply_snapshot(tmp_path, cfg.data_dir)
        finally:
            try:
                os.remove(tmp_path)
            except OSError:
                pass

        marker_payload = {
            "snapshot_blob": remote_blob,
            "synced_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "manifest_blob": cfg.manifest_blob_name,
        }
        _write_local_marker(cfg.marker_file, marker_payload)
        return {
            "enabled": True,
            "updated": True,
            "snapshot_blob": remote_blob,
            "reason": "Applied newer snapshot",
        }
