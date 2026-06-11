#!/bin/sh
# Map Unraid-style PUID/PGID onto OWNER_UID/OWNER_GID, then run the app.
set -e
export OWNER_UID="${OWNER_UID:-${PUID:-1000}}"
export OWNER_GID="${OWNER_GID:-${PGID:-1000}}"
exec uvicorn app.main:app --host "${BIND_HOST:-0.0.0.0}" --port "${BIND_PORT:-8080}"
