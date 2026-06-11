"""Render a tracklist into LRC (or plain) text for embedding as lyrics.

Synced output uses ``[mm:ss.xx] Artist - Title`` (with an hours field for long
mixes, since Navidrome's timestamp regex caps minutes at two digits). With no
timestamps we emit a plain newline-joined tracklist. The whole string is what gets
written into the lyrics tag; Navidrome decides synced-vs-plain by whether any line
starts with a timestamp.
"""

from __future__ import annotations

from .models import Tracklist

# Navidrome caps the lyrics tag at 32768 chars (resources/mappings.yaml).
MAX_LYRICS_CHARS = 32768


def _fmt_timestamp(seconds: float) -> str:
    """Format seconds as an LRC timestamp. Uses [h:mm:ss.xx] past one hour."""
    if seconds < 0:
        seconds = 0.0
    centis = int(round(seconds * 100))
    cs = centis % 100
    total_s = centis // 100
    s = total_s % 60
    total_m = total_s // 60
    m = total_m % 60
    h = total_m // 60
    if h > 0:
        return f"[{h}:{m:02d}:{s:02d}.{cs:02d}]"
    return f"[{m:02d}:{s:02d}.{cs:02d}]"


def _cap(lines: list[str]) -> str:
    """Join lines, dropping any tail that would exceed the lyrics size cap."""
    out: list[str] = []
    length = 0
    for line in lines:
        add = len(line) + (1 if out else 0)
        if length + add > MAX_LYRICS_CHARS:
            break
        out.append(line)
        length += add
    return "\n".join(out)


def render_lrc(tracklist: Tracklist) -> str:
    """Render ``tracklist`` to lyrics text. Empty tracklist → empty string."""
    if not tracklist.tracks:
        return ""

    if tracklist.has_timestamps:
        # Partial timing is common (a 1001tl set may time only a few tracks). Keep the
        # tracklist in its original play order and stamp only the tracks that carry a
        # real time; untimed tracks stay as plain lines in place. This preserves a
        # readable, correctly-ordered list rather than hoisting the timed tracks to the
        # top and dumping the rest at the bottom.
        lines = [
            f"{_fmt_timestamp(t.start)} {t.display()}" if t.start is not None else t.display()
            for t in tracklist.tracks
        ]
        return _cap(lines)

    # Names only → plain tracklist.
    return _cap([t.display() for t in tracklist.tracks])
