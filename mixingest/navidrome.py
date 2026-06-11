"""Notify Navidrome to rescan via the Subsonic ``startScan`` endpoint.

Subsonic token auth: send a random salt ``s`` and ``t = md5(password + salt)``
(never the raw password). Failure here is non-fatal — the mix is already filed and
the scheduled scan will pick it up — so this logs and returns False rather than
raising.
"""

from __future__ import annotations

import hashlib
import secrets
from typing import Callable

import httpx

from .config import SUBSONIC_API_VERSION, SUBSONIC_CLIENT, Config

OnLog = Callable[[str], None]


def _auth_params(user: str, password: str) -> dict[str, str]:
    salt = secrets.token_hex(8)  # 16 hex chars, well over the 6-char minimum
    token = hashlib.md5(f"{password}{salt}".encode("utf-8")).hexdigest()
    return {
        "u": user,
        "t": token,
        "s": salt,
        "v": SUBSONIC_API_VERSION,
        "c": SUBSONIC_CLIENT,
        "f": "json",
    }


def trigger_scan(cfg: Config, *, on_log: OnLog | None = None) -> bool:
    """Trigger a Navidrome library scan. Returns True on success, False otherwise."""

    def log(msg: str) -> None:
        if on_log:
            on_log(msg)

    if not cfg.navidrome_enabled:
        log("navidrome: not configured, skipping rescan")
        return False

    url = f"{cfg.navidrome_url.rstrip('/')}/rest/startScan"
    params = _auth_params(cfg.navidrome_user, cfg.navidrome_pass)
    try:
        resp = httpx.get(url, params=params, timeout=15.0)
        resp.raise_for_status()
        body = resp.json()
        status = body.get("subsonic-response", {}).get("status")
        if status == "ok":
            log("navidrome: scan triggered")
            return True
        err = body.get("subsonic-response", {}).get("error", {})
        log(f"navidrome: scan rejected: {err or body}")
        return False
    except (httpx.HTTPError, ValueError) as exc:
        log(f"navidrome: scan request failed: {exc}")
        return False
