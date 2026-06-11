"""Tier 3 — 1001tracklists *reverse lookup* (stub).

Note: the primary 1001tracklists integration is **input resolution** — when the
ingest URL is itself a 1001tl link, `pipeline.resolve_input` fetches it (via
`fetch.py`), parses the tracklist, and extracts the audio source. That path is live.

This waterfall tier is the *other* direction: given a mix downloaded from a plain
YouTube/SoundCloud link, search 1001tl for the matching page to enrich it. That
needs site search (the most rate-limited / blocked surface), so it's deferred.

Wired up in a later pass — returns None for now.
"""

from __future__ import annotations

from ..models import Tracklist
from .base import ResolveContext


class Tracklists1001Source:
    name = "1001tracklists"

    def resolve(self, ctx: ResolveContext) -> Tracklist | None:
        # TODO(tracklist): POST to FlareSolverr (ctx.config.flaresolverr_url) with the
        # search/page URL, parse the returned HTML for the tracklist + cue times into
        # Track(artist, title, start?). Cache aggressively; respect rate limits.
        return None
