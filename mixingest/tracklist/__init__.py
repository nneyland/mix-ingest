"""Tracklist resolution — a best-available-wins waterfall.

The pipeline calls :func:`resolve_tracklist`; each tier is a small, swappable
:class:`TracklistSource`. Tier 1 (platform data / chapters) is implemented; the
external sources are registered stubs so the waterfall is real and wiring them up
later is a one-file change.
"""

from .base import ResolveContext, TracklistSource, default_sources, resolve_tracklist

__all__ = [
    "ResolveContext",
    "TracklistSource",
    "resolve_tracklist",
    "default_sources",
]
