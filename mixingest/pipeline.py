"""The ingest orchestrator — wires the stages together.

This is the single entry point the frontends (CLI, FastAPI app, future MCP tool)
call. It owns the order of operations, progress reporting, and the
graceful-degradation rule: a thin or missing tracklist never fails an ingest.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from . import index, tl1001
from .cleanup import cleanup_download
from .config import Config
from .download import download_audio
from .errors import DownloadError, ResolveError
from .fetch import Fetcher, FetchError, build_fetcher, fetch_cached
from .filing import file_mix
from .lrc import render_lrc
from .models import (
    DownloadResult,
    IngestResult,
    JobProgress,
    MixMeta,
    ProgressSink,
    Track,
    Tracklist,
    noop_progress,
)
from .navidrome import trigger_scan
from .tagging import write_tags
from .tracklist import ResolveContext, resolve_tracklist


@dataclass
class Overrides:
    """Optional user-supplied metadata that wins over what the source reports."""

    dj: str | None = None
    title: str | None = None
    event: str | None = None
    date: str | None = None  # YYYY-MM-DD
    music_genre: str | None = None

    @classmethod
    def from_dict(cls, data: dict[str, Any] | None) -> "Overrides":
        data = data or {}
        return cls(
            dj=_clean(data.get("dj")),
            title=_clean(data.get("title")),
            event=_clean(data.get("event")),
            date=_clean(data.get("date")),
            music_genre=_clean(data.get("music_genre")),
        )


def _clean(v: Any) -> str | None:
    if v is None:
        return None
    v = str(v).strip()
    return v or None


def _normalise_date(raw: str | None) -> str | None:
    """Accept YYYYMMDD or YYYY-MM-DD; return YYYY-MM-DD or None."""
    if not raw:
        return None
    raw = raw.strip()
    if len(raw) == 8 and raw.isdigit():
        return f"{raw[0:4]}-{raw[4:6]}-{raw[6:8]}"
    return raw


def _build_meta(source_url: str, info: dict[str, Any], ov: Overrides,
                page_meta: "tl1001.PageMeta | None" = None) -> MixMeta:
    # Precedence for each field: explicit override > tracklist-page metadata > source.
    pm = page_meta or tl1001.PageMeta()
    dj = ov.dj or pm.dj or info.get("uploader") or info.get("channel") \
        or info.get("artist") or info.get("creator") or "Unknown Artist"
    title = ov.title or pm.title or info.get("title") or "Untitled Mix"
    date = _normalise_date(ov.date) or _normalise_date(pm.date) or _normalise_date(
        info.get("release_date") or info.get("upload_date")
    )
    event = ov.event or pm.event
    music_genre = ov.music_genre or _clean(info.get("genre"))
    return MixMeta(
        dj=dj.strip(),
        title=title.strip(),
        source_url=source_url,
        event=event,
        date=date,
        music_genre=music_genre,
    )


@dataclass
class InputResolution:
    """The result of resolving an ingest URL to something downloadable.

    For a plain YouTube/SoundCloud link this is a pass-through. For a 1001tracklists
    link it carries the extracted audio URL, the parsed tracklist, and page metadata.
    """

    download_url: str
    source_url: str
    preset_tracklist: Tracklist | None = None
    page_meta: "tl1001.PageMeta | None" = None
    # Alternate audio sources (e.g. SoundCloud) to try if the primary download fails —
    # YouTube's anti-bot wall is common, and 1001tl pages often list a SoundCloud rip too.
    fallback_download_urls: list[str] = field(default_factory=list)


def resolve_input(url: str, cfg: Config, *, fetcher: Fetcher | None = None,
                  on_log=None) -> InputResolution:
    """Resolve an ingest URL. 1001tracklists links are fetched, parsed, and turned
    into (audio URL + tracklist); everything else passes straight through."""
    def log(msg: str) -> None:
        if on_log:
            on_log(msg)

    if not tl1001.is_1001tl_url(url):
        return InputResolution(download_url=url, source_url=url)

    log(f"resolving 1001tracklists link: {url}")
    fetcher = fetcher or build_fetcher(cfg)
    cache = cfg.work_dir / ".tlcache"
    try:
        html = fetch_cached(
            fetcher, url, cache, ttl=cfg.tl_cache_ttl_days * 86400,
            ready_substring="trackValue", on_log=log,
        )
    except FetchError as exc:
        raise ResolveError(
            f"Couldn't get past Cloudflare on 1001tracklists ({exc}). "
            f"Try sharing the YouTube/SoundCloud link directly."
        ) from exc

    sources = tl1001.extract_media_sources(html)
    if not sources:
        raise ResolveError(
            "This 1001tracklists page has no downloadable YouTube/SoundCloud source. "
            "Share the audio link directly instead."
        )
    tracklist = tl1001.parse_tracklist(html)
    page_meta = tl1001.parse_page_meta(html)
    extra = f" (+{len(sources) - 1} fallback)" if len(sources) > 1 else ""
    log(f"1001tracklists: {len(tracklist.tracks)} tracks, audio via {sources[0].kind}{extra}")
    return InputResolution(
        download_url=sources[0].url,
        source_url=url,
        preset_tracklist=tracklist if tracklist.tracks else None,
        page_meta=page_meta,
        fallback_download_urls=[s.url for s in sources[1:]],
    )


def _result_from_index(entry: dict[str, Any], url: str) -> IngestResult:
    """Rebuild a lightweight IngestResult from a recorded index entry (dedupe hit)."""
    n = int(entry.get("track_count") or 0)
    meta = MixMeta(
        dj=entry.get("dj") or "Unknown Artist",
        title=entry.get("title") or "Untitled Mix",
        source_url=url,
        event=entry.get("event"),
        date=entry.get("date"),
    )
    return IngestResult(
        final_path=Path(entry["final_path"]),
        meta=meta,
        # Placeholder tracks preserve the count for the UI without re-resolving.
        tracklist=Tracklist(tracks=[Track(title="") for _ in range(n)],
                            source=entry.get("tracklist_source", "none")),
        synced=bool(entry.get("synced")),
        bitrate_label=entry.get("bitrate", "unknown"),
        navidrome_scanned=False,
        notes=list(entry.get("notes") or []),
    )


def _cleanup_sidecars(download: DownloadResult) -> None:
    for sidecar in (download.thumbnail_path, download.info_json_path):
        if sidecar and sidecar.exists():
            try:
                sidecar.unlink()
            except OSError:
                pass


def ingest(
    url: str,
    cfg: Config,
    *,
    overrides: dict[str, Any] | Overrides | None = None,
    force: bool = False,
    progress: JobProgress | None = None,
    sink: ProgressSink | None = None,
    fetcher: Fetcher | None = None,
) -> IngestResult:
    """Run the full pipeline for ``url`` and return an :class:`IngestResult`.

    ``progress`` (a shared, mutable :class:`JobProgress`) is updated throughout so a
    polling frontend can watch it; ``sink`` is called after each update for push
    frontends. Either may be omitted.
    """
    ov = overrides if isinstance(overrides, Overrides) else Overrides.from_dict(overrides)
    prog = progress or JobProgress()
    emit: ProgressSink = sink or noop_progress

    def step(name: str, pct: int) -> None:
        prog.set(step=name, pct=pct, status="running")
        emit(prog)

    def log(msg: str) -> None:
        prog.line(msg)
        emit(prog)

    notes: list[str] = []
    download: DownloadResult | None = None

    try:
        # 0a. Duplicate guard — skip the whole pipeline if we've filed this before.
        if not force:
            prior = index.lookup(cfg, url)
            if prior is not None:
                result = _result_from_index(prior, url)
                result.notes.append("already ingested — skipped (use force to re-ingest)")
                log(f"already ingested: {result.final_path}")
                prog.result = result.to_dict()
                prog.set(step="already ingested", pct=100, status="done")
                emit(prog)
                return result

        # 0b. Resolve the input (1001tracklists link → audio URL + tracklist) ---
        step("resolving link", 3)
        resolution = resolve_input(url, cfg, fetcher=fetcher, on_log=log)
        download_url = resolution.download_url
        if resolution.preset_tracklist:
            notes.append(f"tracklist from 1001tracklists ({len(resolution.preset_tracklist.tracks)} tracks)")

        # 1. Download (try the primary source, then any fallbacks) --------------
        step("downloading", 5)

        def dl_progress(frac: float, msg: str) -> None:
            # Map the download into the 5–65% band of the overall job.
            prog.set(step=msg, pct=int(5 + frac * 60), status="running")
            emit(prog)

        candidates = [download_url, *resolution.fallback_download_urls]
        last_exc: DownloadError | None = None
        for i, cand in enumerate(candidates):
            if i > 0:
                log(f"primary source failed; trying fallback source ({i}/{len(candidates) - 1})")
                notes.append("used fallback audio source (primary unavailable)")
            log(f"downloading {cand}")
            try:
                download = download_audio(cfg, cand, on_progress=dl_progress, on_log=log)
                download_url = cand  # the source we actually used
                break
            except DownloadError as exc:
                last_exc = exc
                log(f"source failed: {exc}")
        if download is None:
            raise last_exc or DownloadError(f"no usable audio source for {url}")
        log(f"got audio: {download.audio_path.name} ({download.bitrate_label})")

        # 2. Resolve tracklist: prefer the 1001tl preset, else run the waterfall -
        step("resolving tracklist", 66)
        if resolution.preset_tracklist:
            tracklist = resolution.preset_tracklist
            log(f"using 1001tracklists tracklist ({len(tracklist.tracks)} tracks)")
        else:
            ctx = ResolveContext(url=download_url, info=download.info, config=cfg)
            tracklist = resolve_tracklist(ctx, on_log=log)
        if not tracklist.tracks:
            notes.append("no tracklist found — filing with clean metadata only")

        # 3. Build metadata + render LRC ---------------------------------------
        step("preparing tags", 74)
        meta = _build_meta(resolution.source_url, download.info, ov, resolution.page_meta)
        lyrics = render_lrc(tracklist)
        synced = tracklist.has_timestamps and bool(lyrics)
        if lyrics:
            kind = "synced" if synced else "plain"
            log(f"rendered {kind} tracklist ({len(tracklist.tracks)} tracks)")

        # 4. Tag + embed --------------------------------------------------------
        step("tagging + embedding lyrics", 82)
        write_tags(download.audio_path, meta, lyrics, cover_path=download.thumbnail_path)

        # 5. File into the library ---------------------------------------------
        step("filing into library", 92)
        final_path = file_mix(download.audio_path, meta, download.ext, cfg, notes=notes)
        log(f"filed: {final_path}")
        _cleanup_sidecars(download)

        # 6. Notify Navidrome ---------------------------------------------------
        step("notifying navidrome", 97)
        scanned = trigger_scan(cfg, on_log=log)

        result = IngestResult(
            final_path=final_path,
            meta=meta,
            tracklist=tracklist,
            synced=synced,
            bitrate_label=download.bitrate_label,
            navidrome_scanned=scanned,
            notes=notes,
        )
        result_dict = result.to_dict()
        prog.result = result_dict
        index.record(cfg, url, result_dict)
        prog.set(step="done", pct=100, status="done")
        emit(prog)
        return result

    except Exception as exc:  # noqa: BLE001 - record on the job, then re-raise
        # The mix never got filed, so drop its temp download artifacts.
        cleanup_download(download)
        prog.error = str(exc)
        prog.set(step="error", status="error")
        prog.line(f"ERROR: {exc}")
        emit(prog)
        raise
