#!/usr/bin/env bash
#
# Weekly yt-dlp refresh. YouTube changes its player often; a stale yt-dlp is the most
# common cause of broken extraction. Invoked as ROOT by mix-update-ytdlp.service.
#
# The pip upgrade runs AS THE MEDIA USER so the venv stays media-owned (a root-run
# upgrade would re-introduce the ownership bug that broke the first deploy); the
# service restart needs root, which the systemd unit provides.
#
set -euo pipefail

cd /opt/mix
echo "yt-dlp before: $(/opt/mix/.venv/bin/yt-dlp --version 2>/dev/null || echo unknown)"

runuser -u media -- env PATH=/usr/local/bin:/usr/bin:/bin UV_CACHE_DIR=/tmp/uv-cache \
  uv pip install --upgrade yt-dlp

echo "yt-dlp after:  $(/opt/mix/.venv/bin/yt-dlp --version 2>/dev/null || echo unknown)"
systemctl restart mix.service
echo "restarted mix.service"
