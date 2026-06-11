"""The tracklist source protocol and the waterfall that runs them in order."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, Protocol, runtime_checkable

from ..config import Config
from ..models import Tracklist

OnLog = Callable[[str], None]


@dataclass
class ResolveContext:
    """Everything a source might need to find a tracklist.

    ``info`` is the full yt-dlp info dict (chapters, uploader, title, webpage_url…).
    Sources should treat it as read-only.
    """

    url: str
    info: dict[str, Any]
    config: Config

    @property
    def title(self) -> str:
        return self.info.get("title") or ""

    @property
    def uploader(self) -> str:
        return (
            self.info.get("uploader")
            or self.info.get("channel")
            or self.info.get("artist")
            or self.info.get("creator")
            or ""
        )

    @property
    def duration(self) -> float | None:
        d = self.info.get("duration")
        return float(d) if d else None


@runtime_checkable
class TracklistSource(Protocol):
    """A pluggable tracklist resolver.

    ``name`` identifies the tier (and is recorded on the resulting Tracklist).
    ``resolve`` returns a non-empty Tracklist on success, or ``None`` to fall
    through to the next tier. It must not raise for "nothing found"; only an
    unexpected internal error should propagate (and the waterfall swallows even
    those, logging, so one bad source never fails the ingest).
    """

    name: str

    def resolve(self, ctx: ResolveContext) -> Tracklist | None: ...


def default_sources(cfg: Config) -> list[TracklistSource]:
    """Build the ordered waterfall, honouring config gates.

    Imported lazily so optional/stub sources don't add import cost or hard deps.
    """
    from .chapters import ChaptersSource
    from .fingerprint import FingerprintSource
    from .mixesdb import MixesDBSource
    from .tracklists1001 import Tracklists1001Source

    sources: list[TracklistSource] = [
        ChaptersSource(),  # Tier 1 — platform data (implemented)
        MixesDBSource(),   # Tier 2 — MediaWiki (stub)
    ]
    if cfg.flaresolverr_url:
        # Tier 3 — only when a FlareSolverr endpoint is configured.
        sources.append(Tracklists1001Source())
    sources.append(FingerprintSource())  # Tier 4 — interface stub
    return sources


def resolve_tracklist(
    ctx: ResolveContext,
    sources: list[TracklistSource] | None = None,
    *,
    on_log: OnLog | None = None,
) -> Tracklist:
    """Run sources in order; first non-empty result wins.

    Always returns a Tracklist (possibly empty) — never raises for a thin or
    missing tracklist, per SPEC's graceful-degradation rule.
    """
    if sources is None:
        sources = default_sources(ctx.config)

    def log(msg: str) -> None:
        if on_log:
            on_log(msg)

    for source in sources:
        try:
            result = source.resolve(ctx)
        except Exception as exc:  # noqa: BLE001 - a bad source must never fail an ingest
            log(f"tracklist[{source.name}] errored, skipping: {exc}")
            continue
        if result and result.tracks:
            kind = "timestamps" if result.has_timestamps else "names only"
            log(f"tracklist[{source.name}] hit: {len(result.tracks)} tracks ({kind})")
            return result
        log(f"tracklist[{source.name}] miss")

    return Tracklist(tracks=[], source="none")
