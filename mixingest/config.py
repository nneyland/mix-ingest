"""Configuration — loaded once from /opt/mix/.env.

All side-effecting config (paths, URLs, tokens) lives in the env file per SPEC.
`load_config()` is the single entry point; everything downstream takes a `Config`
so the core stays testable and free of global state.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

from .errors import ConfigError

DEFAULT_ENV_PATH = Path("/opt/mix/.env")

# Subsonic client identifier sent on API calls.
SUBSONIC_CLIENT = "mix-ingest"
SUBSONIC_API_VERSION = "1.16.1"


@dataclass(frozen=True)
class Config:
    """Resolved runtime configuration."""

    library_root: Path
    work_dir: Path

    navidrome_url: str | None
    navidrome_user: str | None
    navidrome_pass: str | None

    soundcloud_oauth: str | None
    youtube_cookies: str | None  # path to a Netscape cookies.txt for YouTube auth
    flaresolverr_url: str | None
    ingest_shared_secret: str | None

    bind_host: str
    bind_port: int

    # Days to cache fetched 1001tracklists pages (avoid re-hitting Cloudflare).
    tl_cache_ttl_days: int = 30

    # UID/GID the filed output should be owned by (your media user; Unraid: 99/100).
    owner_uid: int = 1000
    owner_gid: int = 1000

    @property
    def navidrome_enabled(self) -> bool:
        return bool(self.navidrome_url and self.navidrome_user and self.navidrome_pass)


def _clean(value: str | None) -> str | None:
    """Treat empty / whitespace-only env values as unset."""
    if value is None:
        return None
    value = value.strip()
    return value or None


def load_config(env_path: str | os.PathLike[str] | None = DEFAULT_ENV_PATH) -> Config:
    """Load configuration from the env file (if present) and the environment.

    Real env vars win over the file (so systemd's EnvironmentFile or an explicit
    override both work). Missing optional values become ``None``.
    """
    if env_path is not None and Path(env_path).is_file():
        load_dotenv(env_path, override=False)

    library_root = _clean(os.getenv("LIBRARY_ROOT")) or "/data/media/mixes"
    work_dir = _clean(os.getenv("WORK_DIR")) or "/data/inbox/.mixtmp"

    try:
        bind_port = int(_clean(os.getenv("BIND_PORT")) or "8080")
    except ValueError as exc:  # pragma: no cover - defensive
        raise ConfigError(f"BIND_PORT is not an integer: {os.getenv('BIND_PORT')!r}") from exc

    try:
        tl_cache_ttl_days = int(_clean(os.getenv("TL_CACHE_TTL_DAYS")) or "30")
    except ValueError as exc:  # pragma: no cover - defensive
        raise ConfigError(f"TL_CACHE_TTL_DAYS is not an integer: {os.getenv('TL_CACHE_TTL_DAYS')!r}") from exc

    try:
        owner_uid = int(_clean(os.getenv("OWNER_UID")) or "1000")
        owner_gid = int(_clean(os.getenv("OWNER_GID")) or "1000")
    except ValueError as exc:  # pragma: no cover - defensive
        raise ConfigError(
            f"OWNER_UID/OWNER_GID is not an integer: {os.getenv('OWNER_UID')!r}/{os.getenv('OWNER_GID')!r}"
        ) from exc

    return Config(
        library_root=Path(library_root),
        work_dir=Path(work_dir),
        navidrome_url=_clean(os.getenv("NAVIDROME_URL")),
        navidrome_user=_clean(os.getenv("NAVIDROME_USER")),
        navidrome_pass=_clean(os.getenv("NAVIDROME_PASS")),
        soundcloud_oauth=_clean(os.getenv("SOUNDCLOUD_OAUTH")),
        youtube_cookies=_clean(os.getenv("YOUTUBE_COOKIES")),
        flaresolverr_url=_clean(os.getenv("FLARESOLVERR_URL")),
        ingest_shared_secret=_clean(os.getenv("INGEST_SHARED_SECRET")),
        bind_host=_clean(os.getenv("BIND_HOST")) or "0.0.0.0",
        bind_port=bind_port,
        tl_cache_ttl_days=tl_cache_ttl_days,
        owner_uid=owner_uid,
        owner_gid=owner_gid,
    )
