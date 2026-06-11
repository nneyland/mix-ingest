"""Tier 4 — audio fingerprinting (interface stub only).

Last resort when no structured tracklist exists: fingerprint segments of the audio
(AudD / ACRCloud / similar) to recover track names and approximate offsets. Defining
the interface now keeps the waterfall honest; the implementation is deliberately a
TODO per SPEC (don't implement now).
"""

from __future__ import annotations

from ..models import Tracklist
from .base import ResolveContext


class FingerprintSource:
    name = "fingerprint"

    def resolve(self, ctx: ResolveContext) -> Tracklist | None:
        # TODO(tracklist): sample the audio at intervals, query a fingerprinting API,
        # de-duplicate consecutive matches into Track(artist, title, start).
        return None
