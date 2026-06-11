"""Core data models passed between pipeline stages.

These are deliberately plain dataclasses with no I/O — the download, tracklist,
tagging and filing stages all communicate through them.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable


@dataclass
class Track:
    """One entry in a tracklist.

    ``start`` is seconds from the start of the mix, or ``None`` when the source
    gave us names but no timestamps.
    """

    title: str
    artist: str | None = None
    start: float | None = None

    def display(self) -> str:
        """Human label: ``Artist - Title`` when we know the artist, else ``Title``."""
        title = self.title.strip()
        artist = (self.artist or "").strip()
        if artist:
            return f"{artist} - {title}"
        return title


@dataclass
class Tracklist:
    """A resolved tracklist plus where it came from."""

    tracks: list[Track] = field(default_factory=list)
    source: str = "none"  # e.g. "chapters", "mixesdb", "1001tracklists"

    def __bool__(self) -> bool:
        return bool(self.tracks)

    @property
    def has_timestamps(self) -> bool:
        """True if at least one track carries a usable start time → synced LRC."""
        return any(t.start is not None for t in self.tracks)


@dataclass
class MixMeta:
    """The tag scheme for the finished file (SPEC step 4)."""

    dj: str
    title: str
    source_url: str
    event: str | None = None
    date: str | None = None  # YYYY-MM-DD
    music_genre: str | None = None  # optional second genre value alongside "DJ Mix"

    @property
    def genres(self) -> list[str]:
        genres = ["DJ Mix"]
        if self.music_genre:
            genres.append(self.music_genre)
        return genres

    @property
    def comment(self) -> str:
        if self.event:
            return f"{self.source_url} — {self.event}"
        return self.source_url


@dataclass
class DownloadResult:
    """Output of the download stage."""

    audio_path: Path
    info: dict[str, Any]  # the full yt-dlp info dict (chapters live here)
    ext: str
    codec: str | None = None  # acodec, e.g. "opus", "aac", "mp3"
    abr: float | None = None  # average bitrate in kbps, if known
    thumbnail_path: Path | None = None
    info_json_path: Path | None = None

    @property
    def bitrate_label(self) -> str:
        """Honest, human bitrate string for the UI (source quality caps output)."""
        if self.abr:
            return f"~{round(self.abr)} kbps {self.codec or ''}".strip()
        return self.codec or "unknown"


@dataclass
class IngestResult:
    """The outcome of a full ingest, returned to whatever called the core."""

    final_path: Path
    meta: MixMeta
    tracklist: Tracklist
    synced: bool
    bitrate_label: str
    navidrome_scanned: bool
    notes: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "final_path": str(self.final_path),
            "dj": self.meta.dj,
            "title": self.meta.title,
            "event": self.meta.event,
            "date": self.meta.date,
            "tracklist_source": self.tracklist.source,
            "track_count": len(self.tracklist.tracks),
            "synced": self.synced,
            "bitrate": self.bitrate_label,
            "navidrome_scanned": self.navidrome_scanned,
            "notes": list(self.notes),
        }


# --- Progress reporting -------------------------------------------------------
#
# The core reports progress through a tiny structured object. The CLI prints it;
# the web app stores it for polling. Keeping it in the core (not the app) means a
# future MCP wrapper gets the same progress stream for free.

Status = str  # one of: "queued", "running", "done", "error"


@dataclass
class JobProgress:
    """Mutable, structured progress for one ingest."""

    status: Status = "queued"
    pct: int = 0
    step: str = "queued"
    url: str | None = None  # the source link being ingested (for the jobs view)
    log: list[str] = field(default_factory=list)
    result: dict[str, Any] | None = None
    error: str | None = None
    created_at: float = field(default_factory=time.time)
    updated_at: float = field(default_factory=time.time)

    def set(self, *, step: str | None = None, pct: int | None = None,
            status: Status | None = None) -> None:
        if step is not None:
            self.step = step
        if pct is not None:
            self.pct = max(0, min(100, pct))
        if status is not None:
            self.status = status
        self.updated_at = time.time()

    def line(self, message: str) -> None:
        """Append a log line (kept bounded so a chatty download can't grow forever)."""
        self.log.append(message)
        if len(self.log) > 500:
            del self.log[: len(self.log) - 500]
        self.updated_at = time.time()

    def to_dict(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "pct": self.pct,
            "step": self.step,
            "url": self.url,
            "log": list(self.log),
            "result": self.result,
            "error": self.error,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }


# A progress sink is anything that accepts a JobProgress to react to updates.
# The pipeline calls it after each meaningful state change. Defaults to a no-op.
ProgressSink = Callable[["JobProgress"], None]


def noop_progress(_progress: JobProgress) -> None:
    return None
