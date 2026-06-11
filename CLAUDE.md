# mix-ingest — working notes for Claude

Personal DJ-mix ingestion service. Paste a YouTube / SoundCloud / Mixcloud /
1001tracklists link → download best-available audio (no re-encode) → resolve a
tracklist → embed it as **synced lyrics** → tag for clean sorting → file into a
Navidrome "Mixes" library → trigger a rescan. End goal: share a link from an iPhone,
~10 min later stream/offline the mix in Amperfy with a tracklist that scrolls in time.

Full brief: `SPEC.md`. This file is the distilled, persistent version.

## Deployment assumptions (NOT a laptop)

- Runs on a Debian LXC (reference deployment) or in Docker; service runs as a
  dedicated **`media` user (UID/GID 1000)** — Docker/Unraid uses `OWNER_UID`/`OWNER_GID`.
- The host's music share is **bind-mounted at `/data`** rw. Music under `/data/media`;
  mixes go to `/data/media/mixes/`.
- Do ALL temp/working downloads under `/data` (`/data/inbox/.mixtmp/`) — **never** on
  the small root disk.
- **Navidrome** on another host (Subsonic API, e.g. `http://navidrome:4533`). A second
  Navidrome library already points at the mixes folder.
- Exposed via an existing reverse proxy → just bind `:8080`.
- ffmpeg is present; **AtomicParsley is not** (so cover art is embedded via mutagen,
  not yt-dlp's EmbedThumbnail).
- The 1001tracklists fetcher needs **xvfb + xauth** (`apt`) and a Playwright Chromium
  (`uv run playwright install --with-deps chromium`). yt-dlp warns about no JS runtime
  on YouTube — works for now, but installing `deno` is recommended for robustness.
- All side-effecting config (paths, URLs, tokens) lives in `/opt/mix/.env`, loaded once.

## Architecture

- `mixingest/` — the **core library**, frontend-agnostic. Download, resolve tracklist,
  render LRC, tag+embed, file, notify. **No web/CLI assumptions inside it.**
- `app/` — a **thin FastAPI wrapper** (web form + iOS Shortcut endpoint).
- A future MCP tool (`mix_ingest`) will be another thin wrapper over the same core.
  **Design for it; don't build it.**

## Pipeline (mixingest/pipeline.py)

1. **Download** (`download.py`, yt-dlp): `-f bestaudio`, **no re-encode** (keep source
   codec — never fake FLAC, never transcode lossy→lossy). `--write-info-json`
   (chapters live here), write+convert thumbnail to jpg. SoundCloud 256k AAC needs
   `SOUNDCLOUD_OAUTH`. Download into `WORK_DIR`, move on success.
2. **Resolve tracklist:**
   - **1001tracklists link as input** (`pipeline.resolve_input` + `tl1001.py` +
     `fetch.py`): if the ingest URL is a 1001tl (or `1001.tl`) link, fetch the page,
     parse the tracklist *with timestamps*, extract the embedded YouTube/SoundCloud
     audio URL, and download that. The parsed tracklist + page DJ/title/date win over
     the waterfall and the source's own metadata. This is the share-sheet flow.
     - 1001tl is behind **Cloudflare Turnstile**; plain HTTP can't pass. `fetch.py`
       uses **headful Chromium under xvfb** (`pyvirtualdisplay`), or a **FlareSolverr**
       service if `FLARESOLVERR_URL` is set. Fetches are rate-limited + disk-cached.
       Headful+xvfb clears managed Turnstile (~12s); headless and
       `launch_persistent_context` do **not** — use `launch` + `new_context`.
   - Otherwise a **waterfall** (`tracklist/`), best-available wins:
     - Tier 1 — platform data: **YouTube chapters** from info.json `chapters[]`
       (implemented). Mixcloud sections (TODO).
     - Tier 2 — **MixesDB** (MediaWiki `api.php` at `/w/api.php`, not `/db/`). Stub.
     - Tier 3 — **1001tracklists reverse lookup** (find the page from a YT/SC mix). Stub.
     - Tier 4 — audio fingerprinting. Interface stub only.
   - **Graceful degradation:** timestamps → synced LRC; names only → plain tracklist;
     nothing → clean metadata, no lyrics. **Never fail an ingest over a thin tracklist.**
     (A 1001tl link with no downloadable source *does* error, with a clear message.)
3. **Render LRC** (`lrc.py`): `[mm:ss.xx] Artist - Title` sorted by time; no timestamps
   → newline-joined plain tracklist.
4. **Tag + embed** (`tagging.py`, mutagen, format-agnostic): embed the LRC into the
   **lyrics** tag — this is what makes Navidrome + Amperfy scroll the tracklist.
   - Navidrome detects synced lyrics by finding `[mm:ss.xx]` at line starts in the
     PLAIN lyrics tag (no SYLT needed). Lyrics capped at 32768 chars.
   - Per container: ID3 `USLT`; MP4 `\xa9lyr`; Vorbis/FLAC/Opus `LYRICS`+`UNSYNCEDLYRICS`.
   - Tag scheme: Album Artist = DJ · Artist = DJ · Album = title · Title = title ·
     Genre = `DJ Mix` (+ optional music genre) · Date = mix date ·
     Comment = source URL + event.
5. **File** (`filing.py`): `/data/media/mixes/<DJ>/<YYYY-MM-DD - Event - Title>/<title>.<ext>`.
   Sanitise names; keep ownership `1000:1000`.
6. **Notify** (`navidrome.py`): Subsonic `startScan` (token = md5(pass+salt)).

## Conventions

- Core never raises for a thin/missing tracklist; it degrades and records a note.
- Source quality caps output — be honest in the UI about the bitrate actually obtained.
- Be a good citizen: rate-limit + cache MixesDB / 1001tl when those tiers land.

## Run

- Install: `uv venv /opt/mix/.venv && uv sync` (in `/opt/mix`).
- CLI (drive core directly): `uv run mix-ingest <url> [--dj … --title … --event … --date …]`
- App: `uv run uvicorn app.main:app --host 0.0.0.0 --port 8080` (systemd unit provided).
