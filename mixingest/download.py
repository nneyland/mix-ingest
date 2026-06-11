"""Download stage — a thin, opinionated wrapper over yt-dlp's Python API.

Rules from SPEC:
  * ``-f bestaudio`` and **no re-encode** — keep whatever codec the source offers.
  * write the info.json (chapters live there) and a jpg thumbnail.
  * download into WORK_DIR (under /data), never the root disk.
  * SoundCloud Go+ 256k AAC when SOUNDCLOUD_OAUTH is set.
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path
from typing import Any, Callable

from yt_dlp import YoutubeDL
from yt_dlp.utils import DownloadError as _YTDLPDownloadError

from .config import Config
from .errors import DownloadError
from .models import DownloadResult

# Containers mutagen can tag directly. Anything else (notably YouTube's Opus-in-WebM)
# is remuxed — codec copied, never re-encoded — into a taggable container.
_TAGGABLE_EXTS = {".m4a", ".mp4", ".m4b", ".aac", ".mp3", ".flac", ".opus", ".ogg", ".oga"}


def _audio_container_for(codec: str | None) -> str | None:
    """Audio-only, mutagen-taggable container ext that can hold ``codec`` via a copy.

    Used to repackage a non-taggable container (Opus-in-WebM) or strip the video
    stream from a progressive format — always ``-c:a copy`` (never a re-encode).
    Returns ``None`` for codecs with no safe copy target.
    """
    if not codec:
        return None
    c = codec.lower()
    if c.startswith("opus"):
        return ".opus"
    if c.startswith("vorbis"):
        return ".ogg"
    if c.startswith("mp4a") or "aac" in c:
        return ".m4a"
    if c.startswith("flac"):
        return ".flac"
    if c == "mp3" or c.startswith("mp3"):
        return ".mp3"
    return None


# yt-dlp errors that mean "extraction produced no downloadable A/V formats" — the
# fingerprint of YouTube's SABR-only experiment, which strips formats on a cookied
# account. Anonymous extraction usually still works, so we retry without cookies.
def _is_no_formats(msg: str) -> bool:
    m = msg.lower()
    return (
        "requested format is not available" in m
        or "only images are available" in m
        or "no video formats" in m
    )


def _is_bot_wall(msg: str) -> bool:
    """YouTube's "confirm you're not a bot" / sign-in wall — clearable with cookies."""
    m = msg.lower()
    return ("confirm you" in m and "bot" in m) or "sign in to confirm" in m

# Called with (fraction 0.0–1.0, human message) as the download proceeds.
OnProgress = Callable[[float, str], None]
# Called with a single log line (warnings, post-processing notes).
OnLog = Callable[[str], None]


class _YDLLogger:
    """Forwards yt-dlp's warnings/errors to our log sink; drops debug spam."""

    def __init__(self, on_log: OnLog | None) -> None:
        self._on_log = on_log

    def _emit(self, msg: str) -> None:
        if self._on_log and msg:
            self._on_log(msg.strip())

    def debug(self, msg: str) -> None:
        # yt-dlp routes both debug and info here; keep only the post-processing/info lines.
        if msg.startswith("[") and "Deleting" not in msg:
            self._emit(msg)

    def info(self, msg: str) -> None:
        self._emit(msg)

    def warning(self, msg: str) -> None:
        self._emit(msg)

    def error(self, msg: str) -> None:
        self._emit(msg)


def _is_soundcloud(url: str) -> bool:
    return "soundcloud.com" in url.lower()


def _is_youtube(url: str) -> bool:
    u = url.lower()
    return "youtube.com" in u or "youtu.be" in u


def _build_opts(cfg: Config, url: str, hook: Callable[[dict[str, Any]], None],
                logger: _YDLLogger, *, use_cookies: bool = True) -> dict[str, Any]:
    cfg.work_dir.mkdir(parents=True, exist_ok=True)
    opts: dict[str, Any] = {
        # Best audio, single stream, no re-encode (no FFmpegExtractAudio postprocessor).
        "format": "bestaudio/best",
        "paths": {"home": str(cfg.work_dir)},
        # Temp names by id keep things collision-free and filesystem-safe; the final
        # human filename is built later in filing.py.
        "outtmpl": {"default": "%(extractor)s-%(id)s.%(ext)s"},
        "writeinfojson": True,
        "writethumbnail": True,
        # Convert whatever thumbnail format (often webp) to jpg so mutagen can embed it.
        "postprocessors": [{"key": "FFmpegThumbnailsConvertor", "format": "jpg"}],
        "quiet": True,
        "no_warnings": False,
        "noprogress": True,
        "consoletitle": False,
        "ignoreerrors": False,
        "overwrites": True,
        "logger": logger,
        "progress_hooks": [hook],
    }
    if _is_soundcloud(url) and cfg.soundcloud_oauth:
        # Go+ 256k AAC: present the paid account's OAuth token.
        opts["http_headers"] = {"Authorization": f"OAuth {cfg.soundcloud_oauth}"}
    if use_cookies and _is_youtube(url) and cfg.youtube_cookies and Path(cfg.youtube_cookies).is_file():
        # Authenticated requests get past YouTube's "confirm you're not a bot" wall,
        # which datacenter/VPS IPs hit on most real content. See README/.env.example.
        opts["cookiefile"] = cfg.youtube_cookies
        # With cookies, yt-dlp's default `web` client needs the EJS remote challenge
        # solver; the `tv` client returns the same formats (incl. 251 opus) with just
        # cookies + a local JS runtime (deno) — no runtime script downloads. Verified.
        opts.setdefault("extractor_args", {})["youtube"] = {"player_client": ["tv"]}
    return opts


def _normalize_to_audio(audio_path: Path, acodec: str | None, has_video: bool,
                        on_log: OnLog | None) -> Path:
    """Ensure the download is an audio-only file in a mutagen-taggable container.

    Two cases force a remux, both ``ffmpeg -map a -c:a copy`` (audio copied verbatim,
    never re-encoded; any video stream dropped):
      * a non-taggable container (e.g. Opus-in-WebM) → repackaged so mutagen can tag it;
      * a progressive format that still carries video → video stripped, so a video file
        never reaches the library even when only a combined format was available.

    Returns the (possibly new) path; on any failure the original path is returned.
    """
    taggable = audio_path.suffix.lower() in _TAGGABLE_EXTS
    if taggable and not has_video:
        return audio_path
    target_ext = _audio_container_for(acodec)
    if not target_ext:
        # Unknown codec — no safe copy target. Leave it; tagging will report it.
        if has_video and on_log:
            on_log(f"could not strip video (unknown audio codec {acodec!r}); "
                   f"keeping {audio_path.suffix}")
        return audio_path
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        return audio_path

    # Write to a sibling temp so the source ext and target ext can safely coincide.
    tmp = audio_path.with_name(f"{audio_path.stem}.norm{target_ext}")
    try:
        subprocess.run(
            [ffmpeg, "-y", "-v", "error", "-i", str(audio_path),
             "-map", "a", "-c:a", "copy", str(tmp)],
            check=True, capture_output=True,
        )
    except (subprocess.CalledProcessError, OSError) as exc:
        if on_log:
            on_log(f"audio remux failed, keeping {audio_path.suffix}: {exc}")
        tmp.unlink(missing_ok=True)
        return audio_path

    src_ext = audio_path.suffix
    audio_path.unlink(missing_ok=True)
    final = audio_path.with_suffix(target_ext)
    tmp.replace(final)
    if on_log:
        what = "stripped video, " if has_video else ""
        on_log(f"{what}repackaged {src_ext} → {target_ext} (codec copied, no re-encode)")
    return final


def _resolve_output_path(info: dict[str, Any], ydl: YoutubeDL) -> Path:
    """The canonical post-processing output path."""
    downloads = info.get("requested_downloads") or []
    if downloads:
        filepath = downloads[-1].get("filepath")
        if filepath:
            return Path(filepath)
    # Fallback for older yt-dlp shapes.
    if info.get("filepath"):
        return Path(info["filepath"])
    return Path(ydl.prepare_filename(info))


def download_audio(
    cfg: Config,
    url: str,
    *,
    on_progress: OnProgress | None = None,
    on_log: OnLog | None = None,
) -> DownloadResult:
    """Download best-available audio for ``url`` into the work dir.

    Returns a :class:`DownloadResult` with the audio path, full info dict (chapters
    included), codec/bitrate, and sidecar thumbnail/info-json paths. Raises
    :class:`DownloadError` if no usable audio file results.
    """
    last_pct = -1.0

    def hook(d: dict[str, Any]) -> None:
        nonlocal last_pct
        if not on_progress:
            return
        if d.get("status") == "downloading":
            total = d.get("total_bytes") or d.get("total_bytes_estimate")
            done = d.get("downloaded_bytes") or 0
            frac = (done / total) if total else 0.0
            # Throttle: only report on whole-percent changes.
            if frac - last_pct >= 0.01 or frac >= 1.0:
                last_pct = frac
                on_progress(min(frac, 1.0), f"downloading {round(frac * 100)}%")
        elif d.get("status") == "finished":
            last_pct = 1.0
            on_progress(1.0, "download finished, post-processing")

    logger = _YDLLogger(on_log)

    def _extract(use_cookies: bool) -> tuple[dict[str, Any], dict[str, Any], Path]:
        opts = _build_opts(cfg, url, hook, logger, use_cookies=use_cookies)
        with YoutubeDL(opts) as ydl:
            raw = ydl.extract_info(url, download=True)
            if raw is None:
                raise DownloadError(f"yt-dlp returned no info for {url}")
            # Playlists are out of scope for v1: take the first entry if handed one.
            if raw.get("_type") == "playlist" and raw.get("entries"):
                entries = [e for e in raw["entries"] if e]
                if not entries:
                    raise DownloadError(f"No downloadable entries for {url}")
                raw = entries[0]
            return raw, ydl.sanitize_info(raw), _resolve_output_path(raw, ydl)

    def _translate(exc: _YTDLPDownloadError) -> DownloadError:
        msg = str(exc)
        if _is_bot_wall(msg) and not (
            _is_youtube(url) and cfg.youtube_cookies and Path(cfg.youtube_cookies).is_file()
        ):
            return DownloadError(
                "YouTube blocked this download with its anti-bot check. Export a "
                "cookies.txt from a browser signed into a (throwaway) YouTube account "
                "and set YOUTUBE_COOKIES=/path/to/cookies.txt in /opt/mix/.env. "
                "Tip: a SoundCloud source for the same set avoids this entirely."
            )
        return DownloadError(msg)

    have_cookies = bool(
        _is_youtube(url) and cfg.youtube_cookies and Path(cfg.youtube_cookies).is_file()
    )
    # Try anonymous first, then fall back to cookies only if YouTube blocks us. Cookies
    # exist to clear the anti-bot wall on gated content, but they also opt the account
    # into YouTube's SABR-only experiment, which strips the audio-only formats and
    # leaves only a low-bitrate progressive stream. Anonymous extraction (the JS-free
    # `android_vr` client) keeps the good audio-only formats for public videos, so it
    # wins whenever it isn't walled.
    attempts = [False, True] if have_cookies else [False]
    raw = info = audio_path = None
    for i, use_cookies in enumerate(attempts):
        try:
            raw, info, audio_path = _extract(use_cookies=use_cookies)
            break
        except _YTDLPDownloadError as exc:
            msg = str(exc)
            can_retry = i < len(attempts) - 1
            if can_retry and (_is_bot_wall(msg) or _is_no_formats(msg)):
                if on_log:
                    why = "blocked by anti-bot wall" if _is_bot_wall(msg) else "no usable formats"
                    on_log(f"anonymous attempt {why}; retrying with cookies")
                continue
            raise _translate(exc) from exc

    if not audio_path.exists():
        raise DownloadError(f"Expected audio at {audio_path}, but it is missing")

    downloads = info.get("requested_downloads") or [{}]
    chosen = downloads[-1]
    acodec = chosen.get("acodec") or info.get("acodec")
    codec = acodec
    if codec in (None, "none"):
        codec = info.get("ext")
    abr = chosen.get("abr") or info.get("abr")
    # A real vcodec means the chosen format carries video (a progressive fallback) —
    # strip it below so the library only ever holds audio.
    vcodec = chosen.get("vcodec") or info.get("vcodec")
    has_video = vcodec not in (None, "none")

    # Sidecars are keyed by the download stem; resolve them before any remux changes
    # the suffix (the stem itself is unchanged by remuxing).
    thumb = audio_path.with_suffix(".jpg")
    info_json = audio_path.parent / f"{audio_path.stem}.info.json"

    audio_path = _normalize_to_audio(audio_path, acodec, has_video, on_log)

    return DownloadResult(
        audio_path=audio_path,
        info=info,
        ext=audio_path.suffix.lstrip("."),
        codec=codec,
        abr=float(abr) if abr else None,
        thumbnail_path=thumb if thumb.exists() else None,
        info_json_path=info_json if info_json.exists() else None,
    )
