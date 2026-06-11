"""Tier 2 — MixesDB (MediaWiki ``api.php``).

MixesDB is a MediaWiki (1.44 as of writing). We use its public API at
``/w/api.php`` (NOT ``/db/``, which 301-redirects):

  * ``action=query&list=search`` — find candidate pages by uploader/title.
  * ``action=parse&prop=wikitext`` — fetch the matched page's wikitext.

Tracklists live in a ``== Tracklist ==`` section as a ``#``-numbered (or ``<list>``)
block of ``Artist - Title`` lines, occasionally prefixed with a ``[time]`` marker.
Timestamp conventions are *inconsistent* (bare ``[63]`` means minutes, colon forms
mean h:mm:ss / mm:ss, and many pages have none), so we always extract names and only
attach timestamps when every marker parses cleanly, is monotonic, and fits the mix
duration — otherwise we degrade to a names-only (plain) tracklist. We never attach a
*wrong* set: a candidate page must clear a conservative match threshold first.

Be a good citizen: requests are rate-limited and disk-cached.
"""

from __future__ import annotations

import hashlib
import html as _html
import json
import os
import re
import threading
import time
from pathlib import Path
from typing import Any

import httpx

from ..models import Track, Tracklist
from .base import ResolveContext

# MediaWiki API base. Verified live (MediaWiki 1.44); /db/api.php 301-redirects.
MIXESDB_API = "https://www.mixesdb.com/w/api.php"
_UA = os.getenv(
    "MIXESDB_USER_AGENT",
    "mix-ingest/0.1 (+https://github.com/nneyland/mix-ingest; DJ-mix tracklist lookup)",
)

# Polite spacing between MixesDB hits (process-wide).
_rate_lock = threading.Lock()
_last_hit = 0.0
_MIN_INTERVAL = 1.5

# Cache successful API responses; pages rarely change.
_CACHE_TTL = 30 * 86400

# Accept a candidate only when this fraction of our query tokens appear in its title.
_MATCH_THRESHOLD = 0.5

_WS_RE = re.compile(r"\s+")
_DATE_RE = re.compile(r"\b(\d{4}-\d{2}-\d{2})\b")
# A leading "[time]" marker on a track line: "[63]", "[1:02:33]", "[12:45]".
_TS_RE = re.compile(r"^\[\s*(\d{1,2}:\d{2}(?::\d{2})?|\d{1,3})\s*\]\s*")
# Wiki link "[[target|label]]" or "[[page]]" → keep the label/page text.
_WIKILINK_RE = re.compile(r"\[\[(?:[^|\]]*\|)?([^\]]+)\]\]")
_TEMPLATE_RE = re.compile(r"\{\{[^{}]*\}\}")
_REF_RE = re.compile(r"<ref[^>]*>.*?</ref>|<ref[^>]*/>", re.S)
_TAG_RE = re.compile(r"</?[^>]+>")
_TOKEN_RE = re.compile(r"[a-z0-9]+")


def _rate_limit() -> None:
    global _last_hit
    with _rate_lock:
        wait = _MIN_INTERVAL - (time.monotonic() - _last_hit)
        if wait > 0:
            time.sleep(wait)
        _last_hit = time.monotonic()


def _api_get(params: dict[str, str], cache_dir: Path) -> dict[str, Any] | None:
    """GET the MixesDB API with on-disk caching. Returns parsed JSON or None."""
    params = {**params, "format": "json"}
    key = hashlib.sha256(
        (MIXESDB_API + "?" + "&".join(f"{k}={v}" for k, v in sorted(params.items())))
        .encode("utf-8")
    ).hexdigest()[:24]
    cache_dir.mkdir(parents=True, exist_ok=True)
    cache_file = cache_dir / f"{key}.json"
    if cache_file.exists() and (time.time() - cache_file.stat().st_mtime) < _CACHE_TTL:
        try:
            return json.loads(cache_file.read_text("utf-8"))
        except (ValueError, OSError):
            pass
    _rate_limit()
    resp = httpx.get(MIXESDB_API, params=params, headers={"User-Agent": _UA}, timeout=20.0)
    resp.raise_for_status()
    data = resp.json()
    try:
        cache_file.write_text(json.dumps(data), "utf-8")
    except OSError:
        pass
    return data


def _tokens(text: str) -> set[str]:
    return set(_TOKEN_RE.findall(text.lower()))


def _strip_markup(text: str) -> str:
    text = _REF_RE.sub("", text)
    text = _WIKILINK_RE.sub(r"\1", text)
    text = _TEMPLATE_RE.sub("", text)
    text = _TAG_RE.sub("", text)
    text = text.replace("'''", "").replace("''", "")
    text = _html.unescape(text)
    return _WS_RE.sub(" ", text).strip()


def _parse_ts(marker: str) -> float | None:
    """Parse a MixesDB time marker into seconds. Bare integer = minutes."""
    if ":" in marker:
        parts = [int(p) for p in marker.split(":")]
        if len(parts) == 3:
            h, m, s = parts
            return h * 3600 + m * 60 + s
        m, s = parts
        return m * 60 + s
    return int(marker) * 60  # bare number is minutes, per MixesDB convention


def _find_tracklist_section(wikitext: str) -> str:
    """Return the wikitext between '== Tracklist ==' and the next section header."""
    m = re.search(r"==+\s*Tracklist[^=]*==+", wikitext, re.I)
    if not m:
        return ""
    rest = wikitext[m.end():]
    nxt = re.search(r"\n==+[^=]", rest)
    return rest[: nxt.start()] if nxt else rest


def _parse_tracks(wikitext: str) -> tuple[list[Track], bool]:
    """Parse the Tracklist section into tracks. Returns (tracks, timestamps_ok)."""
    section = _find_tracklist_section(wikitext)
    if not section:
        return [], False

    tracks: list[Track] = []
    starts: list[float | None] = []
    for raw in section.splitlines():
        line = raw.strip()
        # Only numbered track lines ('#', '##', …); skip ';' sub-set headers, '*', text.
        if not line.startswith("#"):
            continue
        line = line.lstrip("#").strip()
        if not line:
            continue
        start: float | None = None
        mt = _TS_RE.match(line)
        if mt:
            try:
                start = _parse_ts(mt.group(1))
            except ValueError:
                start = None
            line = line[mt.end():].strip()
        name = _strip_markup(line)
        if not name or name in {"?", "-"}:
            name = "ID"
        elif name.upper() in {"ID - ID", "ID-ID"}:
            name = "ID"
        tracks.append(Track(title=name, artist=None, start=start))
        starts.append(start)

    if not tracks:
        return [], False
    # Timestamps are trustworthy only if every track has one and they are
    # non-decreasing — otherwise keep the names but drop the (suspect) times.
    ts_ok = all(s is not None for s in starts) and all(
        starts[i] <= starts[i + 1] for i in range(len(starts) - 1)  # type: ignore[operator]
    )
    return tracks, ts_ok


def _score(candidate_title: str, query_tokens: set[str], want_date: str | None) -> float:
    cand_tokens = _tokens(candidate_title)
    if not query_tokens:
        return 0.0
    overlap = len(query_tokens & cand_tokens) / len(query_tokens)
    # A matching event date in the page title is a strong corroborating signal.
    if want_date and want_date in candidate_title:
        overlap += 0.25
    return overlap


class MixesDBSource:
    name = "mixesdb"

    def resolve(self, ctx: ResolveContext) -> Tracklist | None:
        uploader = ctx.uploader.strip()
        title = ctx.title.strip()
        if not uploader and not title:
            return None

        cache_dir = ctx.config.work_dir / ".mixesdb-cache"
        query = f"{uploader} {title}".strip()
        data = _api_get(
            {"action": "query", "list": "search", "srsearch": query, "srlimit": "8"},
            cache_dir,
        )
        hits = (data or {}).get("query", {}).get("search", []) if data else []
        if not hits:
            return None

        # Pick the best-scoring candidate above the threshold.
        want_date = None
        md = _DATE_RE.search(title) or _DATE_RE.search(
            (ctx.info.get("release_date") or ctx.info.get("upload_date") or "")
        )
        if md:
            want_date = md.group(1)
        query_tokens = _tokens(query)

        best, best_score = None, 0.0
        for hit in hits:
            s = _score(hit.get("title", ""), query_tokens, want_date)
            if s > best_score:
                best, best_score = hit, s
        if best is None or best_score < _MATCH_THRESHOLD:
            return None

        page = _api_get(
            {"action": "parse", "pageid": str(best["pageid"]), "prop": "wikitext"},
            cache_dir,
        )
        wikitext = (
            (page or {}).get("parse", {}).get("wikitext", {}).get("*", "") if page else ""
        )
        if not wikitext:
            return None

        tracks, ts_ok = _parse_tracks(wikitext)
        # Validate timestamps against the known mix duration before trusting them.
        if ts_ok and ctx.duration:
            last = tracks[-1].start or 0.0
            if last > ctx.duration * 1.1:
                ts_ok = False
        if not tracks:
            return None
        if not ts_ok:
            for t in tracks:
                t.start = None
        return Tracklist(tracks=tracks, source=self.name)
