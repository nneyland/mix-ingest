"""Fetchers that can retrieve Cloudflare-protected pages (1001tracklists).

Plain HTTP can't pass 1001tl's Turnstile, so we need a real browser engine. Two
implementations behind one interface:

  * :class:`PlaywrightFetcher` — headful Chromium under a managed virtual display
    (xvfb). Self-contained, runs natively in the LXC. The default.
  * :class:`FlareSolverrFetcher` — offloads the same browser work to a separate
    FlareSolverr service. Used automatically when ``FLARESOLVERR_URL`` is set.

Both are wrapped by a small disk cache + rate limiter (be a good citizen: 1001tl
blocks after a few rapid requests, and a given mix only needs fetching once).
"""

from __future__ import annotations

import hashlib
import os
import threading
import time
from pathlib import Path
from typing import Callable, Protocol

import httpx

from .config import Config

OnLog = Callable[[str], None]

# A realistic desktop UA; kept consistent so a persisted Cloudflare clearance cookie
# (tied to IP + UA) stays valid across fetches.
_UA = ("Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) "
       "Chrome/148.0.0.0 Safari/537.36")
_STEALTH = "Object.defineProperty(navigator,'webdriver',{get:()=>undefined});"


class FetchError(Exception):
    """The page could not be retrieved."""


class FetchBlocked(FetchError):
    """A Cloudflare challenge could not be cleared within the timeout."""


class Fetcher(Protocol):
    name: str

    def fetch(self, url: str, *, ready_substring: str | None = None,
              on_log: OnLog | None = None) -> str: ...


# --- shared virtual display (one per process, started lazily) ------------------

_display_lock = threading.Lock()
_display = None


def _ensure_display() -> None:
    global _display
    if os.environ.get("DISPLAY"):
        return
    with _display_lock:
        if _display is None and not os.environ.get("DISPLAY"):
            from pyvirtualdisplay import Display

            _display = Display(visible=False, size=(1366, 900))
            _display.start()


# --- rate limiter (process-wide, polite spacing between site hits) -------------

_rate_lock = threading.Lock()
_last_hit = 0.0
_MIN_INTERVAL = 3.0  # seconds between requests to the same protected site


def _rate_limit() -> None:
    global _last_hit
    with _rate_lock:
        wait = _MIN_INTERVAL - (time.monotonic() - _last_hit)
        if wait > 0:
            time.sleep(wait)
        _last_hit = time.monotonic()


def _challenged(html: str) -> bool:
    low = html.lower()
    markers = ("just a moment", "checking your browser", "enable javascript", "turnstile")
    return any(m in low for m in markers) and "trackvalue" not in low


class PlaywrightFetcher:
    """Headful Chromium under a virtual display.

    Uses a fresh ``launch`` + ``new_context`` per call — the approach that reliably
    clears managed Turnstile (a persistent profile, surprisingly, does *not*). Each
    fetch solves the challenge in ~10–15s; the disk cache (``fetch_cached``) avoids
    repeat hits, so fresh-per-call costs nothing on re-ingest.
    """

    name = "playwright"

    def __init__(self, *, page_timeout: float = 45.0, solve_timeout: float = 40.0) -> None:
        self.page_timeout = page_timeout
        self.solve_timeout = solve_timeout

    def fetch(self, url: str, *, ready_substring: str | None = None,
              on_log: OnLog | None = None) -> str:
        from playwright.sync_api import sync_playwright

        _ensure_display()
        _rate_limit()

        def log(msg: str) -> None:
            if on_log:
                on_log(msg)

        with sync_playwright() as p:
            browser = p.chromium.launch(
                headless=False,
                args=["--no-sandbox", "--disable-dev-shm-usage",
                      "--disable-blink-features=AutomationControlled"],
            )
            try:
                ctx = browser.new_context(
                    user_agent=_UA, locale="en-US",
                    viewport={"width": 1366, "height": 900},
                )
                ctx.add_init_script(_STEALTH)
                page = ctx.new_page()
                page.goto(url, wait_until="domcontentloaded", timeout=int(self.page_timeout * 1000))
                deadline = time.monotonic() + self.solve_timeout
                html = ""
                while time.monotonic() < deadline:
                    try:
                        html = page.content()
                    except Exception:  # noqa: BLE001 - mid-navigation; retry
                        html = ""
                    if html and (ready_substring or "") in html and not _challenged(html):
                        break
                    page.wait_for_timeout(1500)
                if not html:
                    raise FetchBlocked(f"no content from {url}")
                if (ready_substring and ready_substring not in html) or _challenged(html):
                    raise FetchBlocked(f"Cloudflare challenge not cleared for {url}")
                log(f"fetched via playwright ({len(html)} bytes)")
                return html
            finally:
                browser.close()


class FlareSolverrFetcher:
    """Delegates the browser work to a FlareSolverr service."""

    name = "flaresolverr"

    def __init__(self, endpoint: str, *, timeout: float = 70.0) -> None:
        self.endpoint = endpoint.rstrip("/")
        self.timeout = timeout

    def fetch(self, url: str, *, ready_substring: str | None = None,
              on_log: OnLog | None = None) -> str:
        _rate_limit()
        payload = {"cmd": "request.get", "url": url, "maxTimeout": int(self.timeout * 1000)}
        try:
            resp = httpx.post(f"{self.endpoint}/v1", json=payload, timeout=self.timeout + 10)
            resp.raise_for_status()
            data = resp.json()
        except (httpx.HTTPError, ValueError) as exc:
            raise FetchError(f"FlareSolverr request failed: {exc}") from exc
        if data.get("status") != "ok":
            raise FetchError(f"FlareSolverr error: {data.get('message')}")
        html = data.get("solution", {}).get("response", "")
        if (ready_substring and ready_substring not in html) or _challenged(html):
            raise FetchBlocked(f"Cloudflare challenge not cleared for {url}")
        if on_log:
            on_log(f"fetched via flaresolverr ({len(html)} bytes)")
        return html


def build_fetcher(cfg: Config) -> Fetcher:
    """Pick the fetcher: FlareSolverr if configured, else native Playwright."""
    if cfg.flaresolverr_url:
        return FlareSolverrFetcher(cfg.flaresolverr_url)
    return PlaywrightFetcher()


# --- caching wrapper -----------------------------------------------------------

def fetch_cached(fetcher: Fetcher, url: str, cache_dir: Path, *, ttl: float,
                 ready_substring: str | None = None, on_log: OnLog | None = None) -> str:
    """Fetch ``url`` through ``fetcher``, caching successful HTML on disk.

    A given tracklist page rarely changes, so caching avoids re-hitting Cloudflare on
    re-ingest and keeps us well-behaved.
    """
    cache_dir.mkdir(parents=True, exist_ok=True)
    key = hashlib.sha256(url.encode("utf-8")).hexdigest()[:24]
    cache_file = cache_dir / f"{key}.html"
    if cache_file.exists() and (time.time() - cache_file.stat().st_mtime) < ttl:
        if on_log:
            on_log("using cached page")
        return cache_file.read_text(encoding="utf-8", errors="replace")
    html = fetcher.fetch(url, ready_substring=ready_substring, on_log=on_log)
    cache_file.write_text(html, encoding="utf-8")
    return html
