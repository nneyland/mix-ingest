"""Work-dir hygiene — keep ``WORK_DIR`` from accumulating temp cruft.

Two layers:
  * :func:`cleanup_download` — remove a single download's leftover artifacts
    (audio + sidecars + .part) when an ingest fails before filing.
  * :func:`sweep_work_dir` — on startup, delete orphaned temp files left by crashes,
    preserving the long-lived caches (``.tlcache``, ``.mixesdb-cache``).
"""

from __future__ import annotations

import time
from pathlib import Path

from .config import Config
from .models import DownloadResult

# Caches and browser state we must NOT sweep (they're reusable, not per-ingest junk).
_PRESERVE = {".tlcache", ".mixesdb-cache", ".browser-profile", ".browser-profile2"}


def cleanup_download(download: DownloadResult | None) -> None:
    """Remove a download's audio + sidecars (called on the failure path)."""
    if download is None:
        return
    for p in (download.audio_path, download.thumbnail_path, download.info_json_path):
        if p:
            try:
                Path(p).unlink(missing_ok=True)
            except OSError:
                pass


def sweep_work_dir(cfg: Config, *, max_age_hours: float = 24.0) -> int:
    """Delete stale top-level temp files in WORK_DIR. Returns the count removed.

    Only touches plain files older than ``max_age_hours`` and never recurses into the
    preserved cache directories. Safe to call at startup.
    """
    work = cfg.work_dir
    if not work.is_dir():
        return 0
    cutoff = time.time() - max_age_hours * 3600
    removed = 0
    for child in work.iterdir():
        if child.name in _PRESERVE:
            continue
        try:
            if child.is_file() and child.stat().st_mtime < cutoff:
                child.unlink(missing_ok=True)
                removed += 1
        except OSError:
            pass
    return removed
