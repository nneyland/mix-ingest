"""File the finished mix into the library and fix ownership.

Layout (SPEC step 5):
    <LIBRARY_ROOT>/<DJ>/<YYYY-MM-DD - Event - Title>/<title>.<ext>

All path components are sanitised; the output (and the directories we create) are
chowned to the media user (1000:1000) so Navidrome and Amperfy can read them.
"""

from __future__ import annotations

import os
import shutil
from pathlib import Path

from .config import Config
from .errors import FilingError
from .models import MixMeta
from .naming import sanitize_component, sanitize_filename


def _folder_name(meta: MixMeta) -> str:
    """Build the per-mix folder: ``<date> - <event> - <title>`` (parts optional)."""
    parts = [p for p in (meta.date, meta.event, meta.title) if p]
    return " - ".join(parts) if parts else meta.title


def _chown_best_effort(path: Path, cfg: Config, notes: list[str] | None) -> None:
    try:
        os.chown(path, cfg.owner_uid, cfg.owner_gid)
    except (PermissionError, OSError) as exc:
        if notes is not None:
            notes.append(f"could not chown {path}: {exc}")


def file_mix(
    src: Path,
    meta: MixMeta,
    ext: str,
    cfg: Config,
    *,
    notes: list[str] | None = None,
) -> Path:
    """Move ``src`` into the library and return the final path.

    Creates ``<DJ>/<folder>/`` as needed, chowning everything we create to the media
    user. Overwrites an existing same-named file (re-ingest of the same mix).
    """
    dj_dir = cfg.library_root / sanitize_component(meta.dj, fallback="Unknown Artist")
    mix_dir = dj_dir / sanitize_component(_folder_name(meta), fallback="Mix")
    filename = sanitize_filename(meta.title, ext, fallback="mix")
    dest = mix_dir / filename

    # Track which dirs we create so we only chown our own, not pre-existing parents.
    created: list[Path] = []
    for d in (cfg.library_root, dj_dir, mix_dir):
        if not d.exists():
            created.append(d)
    try:
        mix_dir.mkdir(parents=True, exist_ok=True)
        if dest.exists():
            dest.unlink()
        shutil.move(str(src), str(dest))
    except OSError as exc:
        raise FilingError(f"Failed to file mix into {dest}: {exc}") from exc

    for d in created:
        _chown_best_effort(d, cfg, notes)
    _chown_best_effort(dest, cfg, notes)

    return dest
