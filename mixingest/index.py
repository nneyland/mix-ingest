"""A small persistent index of ingested sources, for duplicate detection.

Re-sharing a link you already ingested shouldn't re-download and re-file the whole
mix. We keep a tiny JSON index (source key → prior result) next to the library and
consult it *before* the expensive fetch/download. A ``force`` flag bypasses it.

The index is intentionally lightweight (no DB): one JSON file, a process lock, atomic
writes, bounded size. It records enough to reconstruct a useful "already ingested"
result without re-running the pipeline.
"""

from __future__ import annotations

import json
import os
import re
import threading
import time
import urllib.parse
from pathlib import Path
from typing import Any

from .config import Config

_lock = threading.Lock()
_MAX_ENTRIES = 2000
INDEX_FILENAME = ".mixcrab-index.json"

# Strip volatile query params so the same video shared with/without these still matches.
_DROP_PARAMS = {"t", "si", "feature", "utm_source", "utm_medium", "utm_campaign", "utm_term"}
_YT_RE = re.compile(
    r"(?:youtube\.com/(?:watch\?v=|embed/|shorts/)|youtu\.be/)([A-Za-z0-9_-]{11})"
)


def normalize_key(url: str) -> str:
    """A stable identity for a source URL (ignores time-offsets / tracking params)."""
    u = url.strip()
    m = _YT_RE.search(u)
    if m:
        return f"yt:{m.group(1)}"
    parts = urllib.parse.urlsplit(u)
    kept = [(k, v) for k, v in urllib.parse.parse_qsl(parts.query)
            if k.lower() not in _DROP_PARAMS]
    return urllib.parse.urlunsplit((
        parts.scheme.lower(), parts.netloc.lower(),
        parts.path.rstrip("/"), urllib.parse.urlencode(kept), "",
    )) or u


def _index_path(cfg: Config) -> Path:
    return cfg.library_root / INDEX_FILENAME


def _load(cfg: Config) -> dict[str, Any]:
    path = _index_path(cfg)
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text("utf-8"))
        return data if isinstance(data, dict) else {}
    except (ValueError, OSError):
        return {}


def lookup(cfg: Config, url: str) -> dict[str, Any] | None:
    """Return the recorded entry for ``url`` if it exists and the file is still present."""
    with _lock:
        entry = _load(cfg).get(normalize_key(url))
    if not entry:
        return None
    final = entry.get("final_path")
    if final and not Path(final).exists():
        return None  # filed mix was since deleted — let it re-ingest
    return entry


def record(cfg: Config, url: str, result_dict: dict[str, Any]) -> None:
    """Record a successful ingest. ``result_dict`` is ``IngestResult.to_dict()``."""
    with _lock:
        data = _load(cfg)
        data[normalize_key(url)] = {**result_dict, "at": time.time()}
        if len(data) > _MAX_ENTRIES:  # drop oldest by recorded time
            for k in sorted(data, key=lambda k: data[k].get("at", 0))[: len(data) - _MAX_ENTRIES]:
                data.pop(k, None)
        path = _index_path(cfg)
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            tmp = path.with_suffix(".json.tmp")
            tmp.write_text(json.dumps(data, indent=0), "utf-8")
            os.replace(tmp, path)
        except OSError:
            pass  # never fail an ingest over the index
