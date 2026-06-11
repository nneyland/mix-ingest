"""Typed errors for the ingest pipeline.

The guiding rule (SPEC): never fail an ingest over a thin or missing tracklist.
Only genuinely fatal conditions — the download failing, or the file system /
library being unusable — should raise. Tracklist gaps degrade instead.
"""

from __future__ import annotations


class IngestError(Exception):
    """Base class for fatal ingest failures."""


class DownloadError(IngestError):
    """yt-dlp could not produce a usable audio file."""


class FilingError(IngestError):
    """The finished file could not be moved into the library."""


class ConfigError(IngestError):
    """Required configuration is missing or invalid."""


class ResolveError(IngestError):
    """A tracklist-source URL (e.g. 1001tracklists) could not be resolved to audio."""
