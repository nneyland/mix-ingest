"""Filesystem-safe name sanitisation.

We keep Unicode (DJ and track names are full of it) but strip characters that are
illegal or troublesome on common filesystems, collapse whitespace, and trim the
dots/spaces that break Windows/SMB shares.
"""

from __future__ import annotations

import re

# Characters illegal on Windows/SMB and awkward on POSIX paths.
_ILLEGAL = r'<>:"/\\|?*'
_ILLEGAL_RE = re.compile(f"[{re.escape(_ILLEGAL)}]")
_CONTROL_RE = re.compile(r"[\x00-\x1f\x7f]")
_WS_RE = re.compile(r"\s+")

# Reserved device names on Windows (harmless on Linux, but the share is mounted
# elsewhere, so avoid them).
_RESERVED = {
    "con", "prn", "aux", "nul",
    *{f"com{i}" for i in range(1, 10)},
    *{f"lpt{i}" for i in range(1, 10)},
}


def sanitize_component(name: str, *, fallback: str = "Unknown", max_len: int = 120) -> str:
    """Sanitise a single path component (a directory or file stem)."""
    name = _CONTROL_RE.sub("", name)
    name = _ILLEGAL_RE.sub("-", name)
    name = _WS_RE.sub(" ", name).strip()
    # Trailing dots/spaces are stripped by Windows/SMB; remove them ourselves.
    name = name.rstrip(". ").strip()
    if name.lower() in _RESERVED:
        name = f"_{name}"
    if len(name) > max_len:
        name = name[:max_len].rstrip(". ")
    return name or fallback


def sanitize_filename(stem: str, ext: str, *, fallback: str = "mix") -> str:
    """Build a safe ``<stem>.<ext>`` filename. ``ext`` may include a leading dot."""
    ext = ext.lstrip(".")
    safe_stem = sanitize_component(stem, fallback=fallback)
    return f"{safe_stem}.{ext}" if ext else safe_stem
