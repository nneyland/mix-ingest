# mix-ingest

A self-hosted DJ-mix ingestion service. Share a YouTube / SoundCloud / Mixcloud /
1001tracklists link and, a few minutes later, stream or offline-download the mix in
your Subsonic client (Amperfy, Symfonium, the Navidrome web UI, …) — with a tracklist
that **scrolls in time** — from a dedicated Navidrome "Mixes" library.

```
link ─▶ download bestaudio (no re-encode) ─▶ resolve tracklist ─▶ render LRC
     ─▶ embed as synced lyrics + tag ─▶ file into library ─▶ trigger Navidrome rescan
```

- **No transcoding.** Keeps the source codec exactly — "best the source offers" is
  lossy for mixes; we never fake a FLAC and never re-encode lossy→lossy. The UI is
  honest about the bitrate actually obtained.
- **Synced lyrics = the tracklist.** The LRC is embedded into the standard lyrics tag.
  Navidrome detects the `[mm:ss.xx]` timestamps and serves it as synced lyrics, which
  clients scroll in time. No sidecar `.lrc`, no chapters.
- **Graceful degradation.** Timestamps → synced lyrics; names only → plain tracklist;
  nothing → clean metadata, no lyrics. A thin tracklist never fails an ingest.
- **Clean tagging & filing.** Album Artist/Artist = DJ, Album/Title = mix title,
  Genre = `DJ Mix`, filed as `<LIBRARY_ROOT>/<DJ>/<YYYY-MM-DD - Event - Title>/`.

## Tracklist sources

**Share a 1001tracklists link directly** (the share-sheet flow): mix-ingest fetches
the page, parses the tracklist *with timestamps*, extracts the embedded
YouTube/SoundCloud source, downloads it, and embeds the tracklist as synced lyrics.
1001tracklists sits behind Cloudflare Turnstile, so the fetch uses a real
(headful) Chromium under a virtual display — or a [FlareSolverr](https://github.com/FlareSolverr/FlareSolverr)
service if you set `FLARESOLVERR_URL`.

For a plain YouTube/SoundCloud link, a best-available **waterfall** runs instead:

| Tier | Source | Status |
|------|--------|--------|
| 1 | Platform data — YouTube chapters (info.json) | **implemented** |
| 1 | Mixcloud sections (API) | TODO |
| 2 | MixesDB (MediaWiki `api.php`) | stub |
| 3 | 1001tracklists *reverse lookup* (find the page from a YT/SC mix) | stub |
| 4 | Audio fingerprinting (AudD/…) | interface stub |

## Install — Docker / Unraid

A prebuilt image is published to GHCR: `ghcr.io/nneyland/mix-ingest`.
It bundles Python, ffmpeg, Chromium + xvfb (for 1001tracklists), and deno (yt-dlp's
JS runtime). Expect a large image (~2 GB) — that's mostly Chromium and ffmpeg.

### docker compose

See [`docker-compose.yml`](docker-compose.yml) for a complete, commented example:

```yaml
services:
  mix-ingest:
    image: ghcr.io/nneyland/mix-ingest:latest
    ports: ["8080:8080"]
    environment:
      NAVIDROME_URL: http://navidrome:4533
      NAVIDROME_USER: youruser
      NAVIDROME_PASS: yourpass
      OWNER_UID: "99"     # Unraid: nobody
      OWNER_GID: "100"    # Unraid: users
    volumes:
      - /mnt/user/data:/data    # your music share; mixes land in /data/media/mixes
    shm_size: 1g                # required — Chromium crashes on Docker's 64 MB default
    restart: unless-stopped
```

Important notes:

- **`shm_size: 1g` is required** for the 1001tracklists fetcher (Chromium needs more
  shared memory than Docker's 64 MB default). Skip it only if you use FlareSolverr.
- The container runs as **root** by default so it can `chown` finished files to
  `OWNER_UID:OWNER_GID` (Unraid: `99:100`). `PUID`/`PGID` are accepted as aliases.
  You *can* run it with `--user`; then files inherit that UID and the chown is a no-op.
- `LIBRARY_ROOT` defaults to `/data/media/mixes` and `WORK_DIR` to
  `/data/inbox/.mixtmp` — keep both on the same mounted volume so moves are atomic.

### Unraid

Two options:

1. **Template:** copy [`unraid/mix-ingest.xml`](unraid/mix-ingest.xml) to
   `/boot/config/plugins/dockerMan/templates-user/` on your flash drive, then
   **Docker → Add Container** and pick `mix-ingest` from the template dropdown.
   Fill in your Navidrome details; the share path defaults to `/mnt/user/data`.
2. **Compose Manager plugin:** paste the compose file above.

Point a Navidrome library at the mixes folder (e.g. `/mnt/user/data/media/mixes`)
and you're done. The web UI is at `http://<server>:8080`.

## Install — Proxmox LXC / bare metal

Tested on a Debian 12/13 LXC (unprivileged works; privileged only needed if your
bind-mount requires it). Assumes a `media` user (UID/GID 1000) and your music share
mounted read-write at `/data`. Requires [`uv`](https://docs.astral.sh/uv/) and `ffmpeg`.

```bash
git clone https://github.com/nneyland/mix-ingest /opt/mix
cd /opt/mix
cp .env.example .env      # then edit
uv venv .venv && uv sync

# Headless-browser deps for 1001tracklists (Cloudflare Turnstile):
apt-get install -y xvfb xauth
# Install Chromium to a shared path the service user can read:
PLAYWRIGHT_BROWSERS_PATH=/opt/mix/.ms-playwright \
  uv run playwright install --with-deps chromium

# Optional but recommended: deno, yt-dlp's JS runtime for YouTube robustness
curl -fsSL https://deno.land/install.sh | DENO_INSTALL=/usr/local sh

# Run as a service:
cp systemd/mix.service /etc/systemd/system/ && systemctl enable --now mix
# Weekly yt-dlp self-update (YouTube breaks old versions regularly):
cp systemd/mix-update-ytdlp.{service,timer} /etc/systemd/system/ \
  && systemctl enable --now mix-update-ytdlp.timer
```

`scripts/setup.sh` automates the above idempotently.

The 1001tracklists fetcher runs **headful Chromium under xvfb** managed in-process via
`pyvirtualdisplay` — headless Chromium can't clear Turnstile; headful under a virtual
display can. To offload this to a shared FlareSolverr instance instead (e.g. one
already serving Prowlarr), set `FLARESOLVERR_URL` — no local Chromium needed.

## Configuration

All config via environment variables (or a `.env` file next to the code — see
[`.env.example`](.env.example)). Everything except the paths is optional.

| Variable | Default | Purpose |
|----------|---------|---------|
| `LIBRARY_ROOT` | `/data/media/mixes` | Where finished mixes are filed |
| `WORK_DIR` | `/data/inbox/.mixtmp` | Scratch space (same filesystem as library) |
| `NAVIDROME_URL` | — | Subsonic API base, e.g. `http://navidrome:4533` |
| `NAVIDROME_USER` / `NAVIDROME_PASS` | — | Credentials for the rescan call |
| `OWNER_UID` / `OWNER_GID` | `1000`/`1000` | Ownership of filed output (Unraid: 99/100) |
| `SOUNDCLOUD_OAUTH` | — | Go+ token → 256 kbps AAC instead of 128 |
| `YOUTUBE_COOKIES` | — | Path to cookies.txt for YouTube's bot wall |
| `FLARESOLVERR_URL` | — | Use FlareSolverr instead of local Chromium for 1001tl |
| `INGEST_SHARED_SECRET` | — | If set, `POST /ingest` requires `X-Ingest-Secret` |
| `BIND_HOST` / `BIND_PORT` | `0.0.0.0`/`8080` | Where the app listens |
| `MIXESDB_USER_AGENT` | sensible default | UA for MixesDB API lookups |

### YouTube's bot wall

Datacenter IPs hit YouTube's "confirm you're not a bot" wall on most content. Fixes:
prefer SoundCloud links where available, or set `YOUTUBE_COOKIES` to a Netscape
cookies.txt exported from a browser signed into a **throwaway** YouTube account
(export from a private window, then close it so the cookies aren't rotated).

## Usage

**Web:** open `http://<host>:8080`, paste a link, watch progress.

**API** (what the web page and the iOS Shortcut use):

```bash
curl -X POST http://<host>:8080/ingest \
  -H 'Content-Type: application/json' \
  -d '{"url": "https://1001.tl/…"}'        # → {"job_id": "…"}
# poll: GET /jobs/{id}    or long-poll: GET /jobs/{id}/wait
```

Optional body fields: `dj`, `title`, `event`, `date`, `music_genre`, `force`
(re-ingest a previously filed source). Add header `X-Ingest-Secret` if configured.

**iOS Share Sheet:** create a Shortcut that accepts URLs and does a single
"Get contents of URL" POST to `/ingest` with the shared URL — fire-and-forget;
the mix appears in your library a few minutes later.

**CLI** (bare-metal installs):

```bash
uv run mix-ingest "https://1001.tl/vjvchh1"
uv run mix-ingest "https://www.youtube.com/watch?v=…" \
  --dj "Artist" --title "Set name" --event "Festival 2025" --date 2025-06-01
```

Overrides are optional — for a 1001tl link the DJ/title/date come from the page,
otherwise from the source. Overrides always win.

## Layout

- `mixingest/` — the reusable **core library** (frontend-agnostic): download,
  resolve tracklist, render LRC, tag + embed, file, notify.
- `app/` — a thin **FastAPI** wrapper (web form + Shortcut endpoint).
- `systemd/`, `scripts/` — bare-metal deployment bits.
- `SPEC.md` — the original build brief; `CLAUDE.md` — working notes.

## Acceptance tests

- YouTube set **with** chapters → synced LRC, correct DJ/title/date, lands in
  library, Navidrome sees it.
- SoundCloud set **without** a usable tracklist → files cleanly, no crash.
- Tracklist **without timestamps** → plain (unsynced) tracklist in lyrics.
- **No re-encode:** `ffprobe` output codec == source codec.
- **Perms:** output owned `OWNER_UID:OWNER_GID`.
- **Embed:** re-reading the file with mutagen shows the LRC in the lyrics tag.

## Good citizenship

Personal archival use. MixesDB and 1001tracklists requests are rate-limited and
disk-cached; don't hammer them. Respect the platforms' terms where they apply to you.

## License

MIT — see [LICENSE](LICENSE).
