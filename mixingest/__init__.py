"""mixingest — the frontend-agnostic core of mix-ingest.

Download a DJ mix, resolve its tracklist, render synced lyrics, tag and embed,
file it into the library, and notify Navidrome. No web/CLI assumptions live here;
the FastAPI app and a future MCP tool are thin wrappers over `pipeline.ingest`.
"""

from .config import Config, load_config
from .models import (
    DownloadResult,
    IngestResult,
    JobProgress,
    MixMeta,
    Track,
    Tracklist,
)
from .pipeline import ingest

__all__ = [
    "Config",
    "load_config",
    "ingest",
    "Track",
    "Tracklist",
    "MixMeta",
    "DownloadResult",
    "IngestResult",
    "JobProgress",
]
