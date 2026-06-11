# mix-ingest — Build Brief

A kickoff spec for Claude Code. Save this in the repo as `SPEC.md`; distil the
"Runtime environment" and "Architecture" sections into `CLAUDE.md` so they persist
across sessions.

---

## What we're building

A personal DJ-mix ingestion service. Paste or share a **YouTube / SoundCloud /
Mixcloud / 1001tracklists** link → it downloads the best-available audio, resolves
the tracklist, embeds it as **synced lyrics**, tags it for clean sorting, files it
into a dedicated "Mixes" music library, and triggers a Navidrome rescan.

End goal: share a link from an iPhone and, ~10 minutes later, stream or
offline-download the mix — with a tracklist that scrolls in time — in Amperfy.

This is part of an existing homelab. It will **later** be exposed as an MCP
tool on a separate server, so build the core as a reusable library, not a
monolithic script.

## Runtime environment (this is not a laptop — read carefully)

- Runs on a Debian LXC (or any Linux host); the service runs as a dedicated
  **`media` user (UID/GID 1000)**.
- The host's music share is **bind-mounted at `/data`** read-write. Music lives under
  `/data/media`; mixes go to `/data/media/mixes/`.
- Do all temp/working downloads under `/data` (e.g. `/data/inbox/.mixtmp/`) —
  **never** on the small root disk.
- Outbound internet works (YouTube/SoundCloud/MixesDB reachable).
- **Navidrome** runs on another host (e.g. `http://navidrome:4533`, Subsonic API);
  a second Navidrome library already points at the mixes folder.
- Exposed via an existing reverse proxy — you just bind to a local port (e.g. `:8080`).

## Architecture (important)

- `mixingest/` — the **core library**, frontend-agnostic. Download, resolve
  tracklist, render LRC, tag+embed, file. No web/CLI assumptions inside it.
- `app/` — a **thin FastAPI wrapper** over the core (web form + iOS Shortcut
  endpoint).
- A future MCP tool (`mix_ingest`) will be another thin wrapper over the same core.
  **Design for it; don't build it now.**
- All side-effecting config (paths, URLs, tokens) in `/opt/mix/.env`, loaded once.

## The pipeline, step by step

1. **Download (yt-dlp):**
   - `-f bestaudio`, **no re-encoding** — keep the source codec. "Best the source
     offers" is lossy for mixes; never fake a FLAC, never transcode lossy→lossy.
   - `--write-info-json` (chapters live here), embed thumbnail + metadata.
   - SoundCloud free streams cap at 128 kbps; if `SOUNDCLOUD_OAUTH` is set (Go+),
     use it for 256 kbps AAC.
   - Download into the temp dir under `/data`, move on success.

2. **Resolve tracklist — a waterfall, best-available wins:**
   - **Tier 1 — platform's own data:** YouTube chapters from info.json
     `chapters[]` (exact timestamps); Mixcloud "sections" via its API. Best for
     synced lyrics.
   - **Tier 2 — MixesDB (primary structured source):** it's a **MediaWiki** → use
     `api.php` (`action=parse`/`query`), not HTML scraping. Verify the API's real
     shape before committing; tracklists live in page wikitext/templates and need
     light parsing. Coverage skews radio/podcast/classic; timestamps inconsistent.
   - **Tier 3 — 1001tracklists (optional, only if `FLARESOLVERR_URL` set):**
     Cloudflare-walled; route through FlareSolverr. Best for recent festival sets.
     Rate-limit, cache, behave.
   - **Tier 4 — audio fingerprinting:** stub the interface, leave AudD/etc. as a
     TODO. Don't implement now.
   - **Graceful degradation:** timestamps → synced LRC; names only → plain
     (unsynced) tracklist; nothing → file with clean metadata and no lyrics.
     **Never fail an ingest over a thin tracklist.**

3. **Render LRC:** lines `[mm:ss.xx] Artist - Title`, sorted by time. No
   timestamps → newline-joined plain tracklist.

4. **Tag + embed (mutagen), format-agnostic:** embed the LRC into the **lyrics**
   tag — this is what makes Navidrome's web UI *and* Amperfy show a scrolling
   tracklist (synced lyrics only; **not** chapters, **not** a sidecar `.lrc`).
   Tag scheme:
   - Album Artist = DJ · Artist = DJ · Album = mix title · Title = mix title
   - Genre = `DJ Mix` (+ optional music genre as a second value)
   - Date = mix date · Comment = source URL + event

5. **File:** `/data/media/mixes/<DJ>/<YYYY-MM-DD - Event - Title>/<title>.<ext>`.
   Sanitise names for filesystem safety; keep ownership `1000:1000`.

6. **Notify Navidrome:** call the Subsonic `startScan` endpoint (`NAVIDROME_URL`
   + creds) so the mix appears without waiting for the scheduled scan.

## Frontend (FastAPI)

- `POST /ingest {url, …overrides}` → enqueue a job, return a job id. In-process
  `BackgroundTasks` is fine for v1; a real queue is optional.
- `GET /` → minimal page: paste a URL, watch status (websocket or polling).
- `GET /jobs/{id}` → status JSON.
- Must accept a POST from an **iOS Share-Sheet Shortcut** over a VPN or the LAN.
- Bind `:8080` (a reverse proxy fronts it). No login for v1 (LAN/VPN only), but leave a
  hook for an optional `INGEST_SHARED_SECRET` header.

## Config — `/opt/mix/.env`

```
LIBRARY_ROOT=/data/media/mixes
WORK_DIR=/data/inbox/.mixtmp
NAVIDROME_URL=http://navidrome:4533
NAVIDROME_USER=
NAVIDROME_PASS=
SOUNDCLOUD_OAUTH=        # optional — Go+ 256k AAC
FLARESOLVERR_URL=        # optional — enables the 1001tl tier
INGEST_SHARED_SECRET=    # optional
```

## Deliverables

`mixingest/` core + `app/` FastAPI + a `systemd` unit (uvicorn as `media`) +
`.env.example` + README. `pyproject.toml`; install with `uv` into a venv at
`/opt/mix`.

## Acceptance tests (ask me for real URLs)

- YouTube set **with** chapters → synced LRC, correct DJ/title/date, lands in
  library, Navidrome sees it.
- SoundCloud set **without** a usable tracklist → files cleanly, no crash.
- Tracklist exists but **lacks timestamps** → plain (unsynced) tracklist in lyrics.
- **No re-encode:** `ffprobe` shows output codec == source codec.
- **Perms:** output owned `1000:1000`.
- **Embed:** re-reading the file with mutagen shows the LRC in the lyrics tag.

## Out of scope for v1

Audio fingerprinting (stub only) · the MCP wrapper (design for, don't build) ·
playlists/batch URLs (single mix first) · Obsidian companion note (later).

## Be a good citizen

Personal archival use. Rate-limit and cache MixesDB / 1001tl requests; don't
hammer. Source quality caps output — be honest in the UI about the bitrate you
actually got.
