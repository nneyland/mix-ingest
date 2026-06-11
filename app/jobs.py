"""In-memory job store + runner.

v1 keeps jobs in a process-local dict and runs them via FastAPI BackgroundTasks
(SPEC says that's fine). The core reports progress into each job's shared
:class:`JobProgress`, which the frontend polls. Swapping in a real queue later means
changing only this module.
"""

from __future__ import annotations

import threading
import uuid
from typing import Any

from mixingest.config import Config
from mixingest.errors import IngestError
from mixingest.models import JobProgress
from mixingest.pipeline import ingest

# Keep a bounded history so a long-lived process doesn't grow without limit.
_MAX_JOBS = 200


class JobStore:
    def __init__(self) -> None:
        self._jobs: dict[str, JobProgress] = {}
        self._order: list[str] = []
        self._lock = threading.Lock()

    def create(self, url: str | None = None) -> str:
        job_id = uuid.uuid4().hex[:12]
        with self._lock:
            self._jobs[job_id] = JobProgress(status="queued", step="queued", url=url)
            self._order.append(job_id)
            while len(self._order) > _MAX_JOBS:
                old = self._order.pop(0)
                self._jobs.pop(old, None)
        return job_id

    def get(self, job_id: str) -> JobProgress | None:
        with self._lock:
            return self._jobs.get(job_id)

    def recent(self, limit: int = 25) -> list[dict[str, Any]]:
        """A compact, newest-first summary of recent jobs for the status view."""
        with self._lock:
            ids = list(reversed(self._order[-limit:]))
            jobs = [(jid, self._jobs[jid]) for jid in ids if jid in self._jobs]
        out: list[dict[str, Any]] = []
        for jid, p in jobs:
            res = p.result or {}
            out.append({
                "job_id": jid,
                "status": p.status,
                "pct": p.pct,
                "step": p.step,
                "url": p.url,
                "title": res.get("title"),
                "dj": res.get("dj"),
                "error": p.error,
                "created_at": p.created_at,
                "updated_at": p.updated_at,
            })
        return out


store = JobStore()


def run_job(job_id: str, url: str, cfg: Config, overrides: dict[str, Any],
            force: bool = False) -> None:
    """Execute one ingest. Runs in a BackgroundTask; never raises out."""
    prog = store.get(job_id)
    if prog is None:  # evicted before it started
        return
    try:
        ingest(url, cfg, overrides=overrides, force=force, progress=prog)
    except IngestError:
        # pipeline already recorded the error onto `prog`.
        pass
    except Exception as exc:  # noqa: BLE001 - last-resort guard for the worker
        prog.error = str(exc)
        prog.set(step="error", status="error")
