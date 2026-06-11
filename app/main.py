"""FastAPI app — a thin HTTP wrapper over mixingest.

Endpoints:
  POST /ingest        {url, dj?, title?, event?, date?, music_genre?} -> {job_id}
  GET  /jobs/{id}     -> JobProgress JSON (poll this)
  GET  /              -> minimal page: paste a URL, watch progress
  GET  /healthz       -> liveness

Auth: if INGEST_SHARED_SECRET is set, POST /ingest requires header
X-Ingest-Secret. Intended for LAN/VPN use behind a reverse proxy.
"""

from __future__ import annotations

import asyncio
import secrets
from contextlib import asynccontextmanager

from fastapi import BackgroundTasks, FastAPI, Header, HTTPException
from fastapi.responses import HTMLResponse, JSONResponse
from pydantic import BaseModel, Field

from mixingest.cleanup import sweep_work_dir
from mixingest.config import load_config

from .jobs import run_job, store

cfg = load_config()

# How long /jobs/{id}/wait may hold a connection. Kept under a typical 60s proxy
# read-timeout so the long-poll returns cleanly; clients re-poll if still running.
_WAIT_MAX_SECONDS = 50.0


@asynccontextmanager
async def lifespan(_app: FastAPI):
    # Sweep orphaned temp files left by any prior crash before serving.
    try:
        sweep_work_dir(cfg)
    except Exception:  # noqa: BLE001 - never block startup on cleanup
        pass
    yield


app = FastAPI(title="mix-ingest", version="0.1.0", lifespan=lifespan)


class IngestRequest(BaseModel):
    url: str = Field(..., description="YouTube / SoundCloud / Mixcloud / … link")
    dj: str | None = None
    title: str | None = None
    event: str | None = None
    date: str | None = None
    music_genre: str | None = None
    force: bool = False  # re-ingest even if this source was filed before


def _check_secret(provided: str | None) -> None:
    if not cfg.ingest_shared_secret:
        return
    if not provided or not secrets.compare_digest(provided, cfg.ingest_shared_secret):
        raise HTTPException(status_code=401, detail="invalid or missing X-Ingest-Secret")


@app.post("/ingest")
def create_ingest(
    body: IngestRequest,
    background: BackgroundTasks,
    x_ingest_secret: str | None = Header(default=None),
) -> JSONResponse:
    _check_secret(x_ingest_secret)
    url = body.url.strip()
    if not url:
        raise HTTPException(status_code=422, detail="url is required")

    job_id = store.create(url)
    overrides = body.model_dump(exclude={"url", "force"}, exclude_none=True)
    background.add_task(run_job, job_id, url, cfg, overrides, body.force)
    return JSONResponse({"job_id": job_id}, status_code=202)


@app.get("/jobs")
def list_jobs(limit: int = 25) -> JSONResponse:
    """Recent jobs, newest first — powers the status view."""
    return JSONResponse({"jobs": store.recent(limit)})


@app.get("/jobs/{job_id}")
def get_job(job_id: str) -> JSONResponse:
    prog = store.get(job_id)
    if prog is None:
        raise HTTPException(status_code=404, detail="unknown job id")
    return JSONResponse(prog.to_dict())


@app.get("/jobs/{job_id}/wait")
async def wait_job(job_id: str) -> JSONResponse:
    """Long-poll: block until the job finishes or ~50s elapse, then return its state.

    Lets the iOS Shortcut show a single ✓/✗ with at most a couple of requests rather
    than a busy poll loop. Returns the same shape as GET /jobs/{id}.
    """
    deadline = asyncio.get_event_loop().time() + _WAIT_MAX_SECONDS
    while True:
        prog = store.get(job_id)
        if prog is None:
            raise HTTPException(status_code=404, detail="unknown job id")
        if prog.status in ("done", "error") or asyncio.get_event_loop().time() >= deadline:
            return JSONResponse(prog.to_dict())
        await asyncio.sleep(1.0)


@app.get("/healthz")
def healthz() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/", response_class=HTMLResponse)
def index() -> str:
    return INDEX_HTML


INDEX_HTML = """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>mix-ingest</title>
<style>
  :root { color-scheme: dark; }
  body { font: 16px/1.5 system-ui, sans-serif; max-width: 640px; margin: 2rem auto;
         padding: 0 1rem; background: #14151a; color: #e9e9ef; }
  h1 { font-size: 1.4rem; } h1 small { color: #8b8b9a; font-weight: 400; }
  form { display: grid; gap: .5rem; }
  input { padding: .6rem .7rem; border: 1px solid #33343f; border-radius: 8px;
          background: #1d1e26; color: inherit; font: inherit; }
  .row { display: grid; grid-template-columns: 1fr 1fr; gap: .5rem; }
  button { padding: .6rem 1rem; border: 0; border-radius: 8px; background: #5b6cff;
           color: #fff; font: inherit; font-weight: 600; cursor: pointer; }
  button:disabled { opacity: .5; cursor: default; }
  details { margin: .25rem 0 .75rem; color: #8b8b9a; }
  #status { margin-top: 1.25rem; display: none; }
  .bar { height: 8px; background: #2a2b35; border-radius: 99px; overflow: hidden; }
  .bar > div { height: 100%; width: 0; background: #5b6cff; transition: width .3s; }
  .step { margin: .5rem 0 .25rem; font-weight: 600; }
  pre { background: #1d1e26; border: 1px solid #33343f; border-radius: 8px;
        padding: .6rem; max-height: 240px; overflow: auto; font-size: 13px;
        white-space: pre-wrap; }
  .ok { color: #4ade80; } .err { color: #f87171; }
  a { color: #8ea2ff; }
  .recent { display: grid; gap: .35rem; }
  .job { display: flex; gap: .5rem; align-items: baseline; font-size: 13px;
         padding: .4rem .6rem; background: #1d1e26; border: 1px solid #2a2b35;
         border-radius: 8px; }
  .job .lbl { flex: 1; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
  .job .st { color: #8b8b9a; }
  .dot { width: .6rem; height: .6rem; border-radius: 99px; flex: none; }
  .d-done { background: #4ade80; } .d-error { background: #f87171; }
  .d-running, .d-queued { background: #5b6cff; }
</style>
</head>
<body>
<h1>mix-ingest <small>— paste a mix link</small></h1>
<form id="f">
  <input id="url" type="url" placeholder="https://www.youtube.com/watch?v=…" required>
  <details>
    <summary>Optional overrides</summary>
    <div class="row" style="margin-top:.5rem">
      <input id="dj" placeholder="DJ / artist">
      <input id="title" placeholder="Mix title">
    </div>
    <div class="row" style="margin-top:.5rem">
      <input id="event" placeholder="Event (e.g. Tomorrowland 2025)">
      <input id="date" placeholder="Date (YYYY-MM-DD)">
    </div>
  </details>
  <button id="go" type="submit">Ingest</button>
</form>

<div id="status">
  <div class="step" id="step">queued</div>
  <div class="bar"><div id="fill"></div></div>
  <div id="result"></div>
  <pre id="log"></pre>
</div>

<h2 style="font-size:1rem;color:#8b8b9a;margin-top:2rem">Recent</h2>
<div id="recent" class="recent">…</div>

<script>
const $ = (id) => document.getElementById(id);
let timer = null;

$("f").addEventListener("submit", async (e) => {
  e.preventDefault();
  $("go").disabled = true;
  $("status").style.display = "block";
  $("result").innerHTML = ""; $("log").textContent = "";
  const body = { url: $("url").value };
  for (const k of ["dj","title","event","date"]) {
    if ($(k).value.trim()) body[k] = $(k).value.trim();
  }
  const r = await fetch("/ingest", {
    method: "POST", headers: {"Content-Type": "application/json"},
    body: JSON.stringify(body),
  });
  if (!r.ok) {
    $("step").innerHTML = '<span class="err">request rejected (' + r.status + ')</span>';
    $("go").disabled = false; return;
  }
  const { job_id } = await r.json();
  poll(job_id);
});

function poll(id) {
  clearInterval(timer);
  timer = setInterval(async () => {
    const r = await fetch("/jobs/" + id);
    if (!r.ok) return;
    const j = await r.json();
    $("step").textContent = j.step + "  (" + j.pct + "%)";
    $("fill").style.width = j.pct + "%";
    $("log").textContent = (j.log || []).join("\\n");
    $("log").scrollTop = $("log").scrollHeight;
    if (j.status === "done" || j.status === "error") {
      clearInterval(timer);
      $("go").disabled = false;
      if (j.status === "done" && j.result) {
        const x = j.result;
        $("result").innerHTML = '<p class="ok">✓ ' + esc(x.dj) + ' — ' + esc(x.title) +
          '</p><p>tracklist: ' + esc(x.tracklist_source) + ' (' + x.track_count +
          ' tracks, ' + (x.synced ? 'synced' : 'plain/none') + ') · ' + esc(x.bitrate) +
          ' · navidrome: ' + (x.navidrome_scanned ? 'scanned' : 'not scanned') + '</p>';
      } else {
        $("result").innerHTML = '<p class="err">✗ ' + esc(j.error || "failed") + '</p>';
      }
      loadRecent();
    }
  }, 1500);
}
function esc(s){ const d=document.createElement("div"); d.textContent=s==null?"":s; return d.innerHTML; }

async function loadRecent() {
  let jobs;
  try { jobs = (await (await fetch("/jobs?limit=15")).json()).jobs; }
  catch (e) { return; }
  if (!jobs.length) { $("recent").textContent = "no ingests yet"; return; }
  $("recent").innerHTML = jobs.map((j) => {
    const label = (j.dj && j.title) ? (j.dj + " — " + j.title) : (j.url || j.job_id);
    const st = j.status === "error" ? (j.error || "error")
             : j.status === "done" ? "done" : (j.step + " " + j.pct + "%");
    return '<div class="job"><span class="dot d-' + esc(j.status) + '"></span>' +
           '<span class="lbl">' + esc(label) + '</span>' +
           '<span class="st">' + esc(st) + '</span></div>';
  }).join("");
}
loadRecent();
setInterval(loadRecent, 5000);
</script>
</body>
</html>"""
