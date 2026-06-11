"""1001tracklists — URL detection, HTML parsing, and source-media extraction.

This module is deliberately *fetch-agnostic*: it turns already-fetched page HTML
into a :class:`Tracklist`, a downloadable source URL, and mix metadata. Getting the
HTML past Cloudflare is the fetcher's job (see ``fetch.py``).

The tracklist data lives in the page HTML: each track row carries a hidden
``..._cue_seconds`` input (exact start time) and a ``trackValue`` span
(``Artist - Title`` plus optional label). ``ID - ID`` marks an unknown track.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from .models import Track, Tracklist

# Hosts that identify a 1001tracklists link (full site + the 1001.tl short links).
_HOST_RE = re.compile(r"(?:^|\.)(?:1001tracklists\.com|1001\.tl)$", re.I)

_ROW_SPLIT = re.compile(r'(?=<div id="tlp_\d+" class="tlpTog)')
_TRACKVALUE_RE = re.compile(r'<span class="trackValue[^"]*"[^>]*>(.*?)</span>\s*</div>', re.S)
_CUE_RE = re.compile(r'_cue_seconds"\s+type="hidden"\s+value="(\d+)"')
_TAG_RE = re.compile(r"<[^>]+>")
_WS_RE = re.compile(r"\s+")

_EMBED_YT_RE = re.compile(r'itemprop="embedUrl"\s+content="https://www\.youtube\.com/embed/([A-Za-z0-9_-]{11})"')
_ANY_YT_RE = re.compile(r'(?:youtube\.com/(?:watch\?v=|embed/)|youtu\.be/)([A-Za-z0-9_-]{11})')
_SC_TRACK_RE = re.compile(r'api\.soundcloud\.com/tracks/(\d+)')
_OG_TITLE_RE = re.compile(r'<meta property="og:title" content="([^"]+)"')
_TITLE_RE = re.compile(r"<title>(.*?)</title>", re.S)
_DATE_RE = re.compile(r"(\d{4}-\d{2}-\d{2})")

# Cloudflare challenge fingerprints — used to recognise a non-real page.
_CHALLENGE_RE = re.compile(
    r"(?i)just a moment|checking your browser|cf-mitigated|enable javascript|turnstile|challenge-platform"
)


def is_1001tl_url(url: str) -> bool:
    m = re.match(r"https?://([^/]+)", url.strip(), re.I)
    if not m:
        return False
    host = m.group(1).lower()
    return bool(_HOST_RE.search(host))


def looks_like_challenge(html: str) -> bool:
    """True if the HTML is a Cloudflare interstitial rather than a real page."""
    return bool(_CHALLENGE_RE.search(html)) and "trackValue" not in html


def _clean_text(fragment: str) -> str:
    import html as _html

    text = _TAG_RE.sub("", fragment)
    text = _html.unescape(text)
    return _WS_RE.sub(" ", text).strip()


def parse_tracklist(html: str) -> Tracklist:
    """Parse track rows into a :class:`Tracklist` (empty if none found).

    1001tl stores each row's start in a ``_cue_seconds`` hidden input, but uses ``0``
    as the *sentinel for "no known timestamp"* — untimed tracks all carry ``0``, not a
    real 0:00. Taking those at face value stamps every untimed track ``[00:00.00]``,
    which collapses the synced lyric onto the last 0:00 line. So a ``0`` counts as a
    real start (0.0) only for the opening track of a set that actually has timing
    (some other row has a positive cue); everywhere else ``0`` means untimed (``None``).
    A set with no positive cues thus degrades cleanly to a plain (un-synced) tracklist.
    """
    names: list[str] = []
    raw_cues: list[int | None] = []
    for seg in _ROW_SPLIT.split(html):
        if 'class="tlpTog' not in seg:
            continue
        mv = _TRACKVALUE_RE.search(seg)
        if not mv:
            continue
        name = _clean_text(mv.group(1))
        if not name or name.upper() in {"ID - ID", "ID-ID"}:
            name = "ID"
        mc = _CUE_RE.search(seg)
        names.append(name)
        raw_cues.append(int(mc.group(1)) if mc else None)

    has_timing = any(c for c in raw_cues if c)  # any positive cue
    tracks: list[Track] = []
    for i, (name, cue) in enumerate(zip(names, raw_cues)):
        if cue:  # positive → a real start time
            start: float | None = float(cue)
        elif cue == 0 and i == 0 and has_timing:  # genuine mix-opening 0:00
            start = 0.0
        else:  # 0-sentinel or no cue → untimed
            start = None
        tracks.append(Track(title=name, artist=None, start=start))
    return Tracklist(tracks=tracks, source="1001tracklists")


@dataclass
class MediaSource:
    url: str
    kind: str  # "youtube" | "soundcloud"


def extract_media_sources(html: str) -> list[MediaSource]:
    """All downloadable sources for the set, best-first (SoundCloud, then YouTube).

    SoundCloud is preferred: free SoundCloud serves up to 160k AAC (vs YouTube's 128k)
    and avoids YouTube's anti-bot wall, which datacenter IPs hit constantly. YouTube is
    kept as a fallback for sets with no (or an unavailable) SoundCloud source. Empty if
    neither is present — the user only shares 1001tl links that have a YT/SC source.
    """
    sources: list[MediaSource] = []
    m = _SC_TRACK_RE.search(html)
    if m:
        # yt-dlp resolves the numeric SoundCloud track id via this API URL form.
        sources.append(MediaSource(f"https://api.soundcloud.com/tracks/{m.group(1)}", "soundcloud"))
    m = _EMBED_YT_RE.search(html) or _ANY_YT_RE.search(html)
    if m:
        sources.append(MediaSource(f"https://www.youtube.com/watch?v={m.group(1)}", "youtube"))
    return sources


def extract_media_url(html: str) -> MediaSource | None:
    """The single best downloadable source (YouTube preferred), or ``None``."""
    sources = extract_media_sources(html)
    return sources[0] if sources else None


@dataclass
class PageMeta:
    dj: str | None = None
    title: str | None = None
    event: str | None = None
    date: str | None = None


def parse_page_meta(html: str) -> PageMeta:
    """Pull DJ / title / date from the page's og:title.

    1001tl titles read like ``DJ @ Event, Location YYYY-MM-DD``. We split on `` @ ``
    and peel the trailing date, mapping the event/venue to the mix title.
    """
    mt = _OG_TITLE_RE.search(html)
    raw = mt.group(1) if mt else (_TITLE_RE.search(html).group(1).strip() if _TITLE_RE.search(html) else "")
    import html as _html

    raw = _html.unescape(raw).strip()
    if not raw:
        return PageMeta()

    date = None
    md = _DATE_RE.search(raw)
    if md:
        date = md.group(1)
        raw = raw.replace(md.group(1), "").strip()

    dj = None
    title = raw
    if " @ " in raw:
        dj, title = raw.split(" @ ", 1)
        dj = dj.strip() or None
    title = title.strip(" ,-").strip() or None
    return PageMeta(dj=dj, title=title, event=None, date=date)
