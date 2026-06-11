"""Command-line front-end — drives the core directly, no web layer.

    mix-ingest <url> [--dj … --title … --event … --date … --music-genre …]

Handy for testing the pipeline and for one-off ingests on the box.
"""

from __future__ import annotations

import argparse
import json
import sys

from .config import load_config
from .errors import IngestError
from .models import JobProgress
from .pipeline import Overrides, ingest


def _make_printer():
    """A progress sink that prints step/pct changes and new log lines to stderr."""
    state = {"step": None, "pct": -1, "log_len": 0}

    def sink(prog: JobProgress) -> None:
        for line in prog.log[state["log_len"]:]:
            print(f"  · {line}", file=sys.stderr)
        state["log_len"] = len(prog.log)
        if prog.step != state["step"] or prog.pct != state["pct"]:
            print(f"[{prog.pct:3d}%] {prog.step}", file=sys.stderr)
            state["step"] = prog.step
            state["pct"] = prog.pct

    return sink


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="mix-ingest", description="Ingest a DJ mix into the library."
    )
    parser.add_argument("url", help="YouTube / SoundCloud / Mixcloud / … link")
    parser.add_argument("--dj", help="Override the DJ / album artist")
    parser.add_argument("--title", help="Override the mix title")
    parser.add_argument("--event", help="Event name (e.g. 'Tomorrowland 2025')")
    parser.add_argument("--date", help="Mix date (YYYY-MM-DD)")
    parser.add_argument("--music-genre", dest="music_genre",
                        help="Optional music genre alongside 'DJ Mix'")
    parser.add_argument("--env", help="Path to the .env file (default /opt/mix/.env)")
    parser.add_argument("--force", action="store_true",
                        help="Re-ingest even if this source was ingested before")
    parser.add_argument("--json", action="store_true",
                        help="Print the result as JSON on stdout")
    args = parser.parse_args(argv)

    cfg = load_config(args.env) if args.env else load_config()
    overrides = Overrides(
        dj=args.dj, title=args.title, event=args.event,
        date=args.date, music_genre=args.music_genre,
    )
    progress = JobProgress()

    try:
        result = ingest(args.url, cfg, overrides=overrides, force=args.force,
                        progress=progress, sink=_make_printer())
    except IngestError as exc:
        print(f"ingest failed: {exc}", file=sys.stderr)
        return 1

    if args.json:
        print(json.dumps(result.to_dict(), indent=2))
    else:
        print(f"\n✓ {result.meta.dj} — {result.meta.title}", file=sys.stderr)
        print(f"  filed: {result.final_path}", file=sys.stderr)
        print(f"  tracklist: {result.tracklist.source} "
              f"({len(result.tracklist.tracks)} tracks, "
              f"{'synced' if result.synced else 'plain/none'})", file=sys.stderr)
        print(f"  bitrate: {result.bitrate_label}", file=sys.stderr)
        print(f"  navidrome: {'scanned' if result.navidrome_scanned else 'not scanned'}",
              file=sys.stderr)
        for note in result.notes:
            print(f"  note: {note}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
