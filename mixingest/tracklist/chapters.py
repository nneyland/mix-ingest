"""Tier 1 — tracklist from the platform's own chapter data (info.json ``chapters[]``).

This is the best source for synced lyrics: YouTube chapters carry exact start
timestamps. We parse each chapter title into ``Artist - Title`` where we can.
"""

from __future__ import annotations

import re

from ..models import Track, Tracklist
from .base import ResolveContext

# Leading "01. ", "1) ", "12 - ", "#3 " index prefixes DJs add to chapter titles.
_INDEX_PREFIX = re.compile(r"^\s*#?\d{1,3}\s*[.)\-:]?\s+")
# A leading "[0:00]" / "0:00 " timestamp some uploaders bake into the title text.
_LEADING_TS = re.compile(r"^\s*\[?\d{1,2}:\d{2}(?::\d{2})?\]?\s+")


def clean_chapter_title(raw: str) -> str:
    """Strip index/timestamp noise from a chapter title, preserving the rest verbatim.

    We deliberately do NOT split into artist/title: chapter conventions are
    inconsistent (some uploaders write "Artist - Title", others "Title - Artist"),
    and the chapter text is already a clean, human-readable label. Reordering it
    would corrupt as often as it helps, so we keep the uploader's own formatting.
    """
    text = raw.strip()
    text = _LEADING_TS.sub("", text)
    text = _INDEX_PREFIX.sub("", text).strip()
    return text or raw.strip()


class ChaptersSource:
    name = "chapters"

    def resolve(self, ctx: ResolveContext) -> Tracklist | None:
        chapters = ctx.info.get("chapters") or []
        if not chapters:
            return None

        tracks: list[Track] = []
        for ch in chapters:
            title = (ch.get("title") or "").strip()
            if not title:
                continue
            start = ch.get("start_time")
            tracks.append(
                Track(
                    title=clean_chapter_title(title),
                    artist=None,
                    start=float(start) if start is not None else None,
                )
            )

        if not tracks:
            return None
        return Tracklist(tracks=tracks, source=self.name)
