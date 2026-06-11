#!/usr/bin/env bash
#
# mix-ingest provisioning — idempotent, run as root on the host.
#
# Encodes the gotcha that bit us once: the service runs as the unprivileged `media`
# user, so everything it touches (the venv, the Playwright browsers, the work dir)
# must be MEDIA-OWNED and live where the systemd unit expects it. Installing as root
# without dropping to `media` is what broke the first deploy.
#
#   sudo /opt/mix/scripts/setup.sh        # (this box has no sudo: just run as root)
#
set -euo pipefail

PROJ=/opt/mix
BROWSERS=$PROJ/.ms-playwright          # matches PLAYWRIGHT_BROWSERS_PATH in mix.service
WORK=/data/inbox/.mixtmp               # matches WORK_DIR in .env
SVC_USER=media

run_as_media() {  # run a command as the service user with a sane PATH + caches on /data
  runuser -u "$SVC_USER" -- env \
    PATH=/usr/local/bin:/usr/bin:/bin \
    PLAYWRIGHT_BROWSERS_PATH="$BROWSERS" \
    UV_CACHE_DIR=/tmp/uv-cache \
    "$@"
}

echo "==> 1/7 system packages (xvfb/xauth for headful Chromium, ffmpeg, unzip)"
apt-get update -qq
apt-get install -y xvfb xauth ffmpeg unzip curl

echo "==> 2/7 deno (JS runtime so yt-dlp's YouTube extractor stays robust)"
if ! command -v deno >/dev/null 2>&1; then
  curl -fsSL https://github.com/denoland/deno/releases/latest/download/deno-x86_64-unknown-linux-gnu.zip -o /tmp/deno.zip
  unzip -o /tmp/deno.zip -d /usr/local/bin
  chmod +x /usr/local/bin/deno
  rm -f /tmp/deno.zip
fi
deno --version | head -1

echo "==> 3/7 uv on a system path so the media user can run it"
if [ ! -x /usr/local/bin/uv ]; then
  if [ -x /root/.local/bin/uv ]; then
    install -m 0755 /root/.local/bin/uv /usr/local/bin/uv
  else
    curl -fsSL https://astral.sh/uv/install.sh | env UV_INSTALL_DIR=/usr/local/bin sh
  fi
fi
uv --version

echo "==> 4/7 hand the project + work dir to the service user"
chown -R "$SVC_USER:$SVC_USER" "$PROJ"
chmod 600 "$PROJ/.env" 2>/dev/null || true
mkdir -p "$WORK"
chown -R "$SVC_USER:$SVC_USER" "$WORK"

echo "==> 5/7 venv + deps (as media, so the venv is media-owned and self-updatable)"
cd "$PROJ"
run_as_media uv venv .venv
run_as_media uv sync

echo "==> 6/7 Playwright Chromium — system libs as root, browser as media"
# install-deps needs apt (root); the browser download must land in the media-owned path.
"$PROJ/.venv/bin/python" -m playwright install-deps chromium
run_as_media uv run playwright install chromium
test -x "$BROWSERS"/chromium-*/chrome-linux64/chrome && echo "  chromium present at $BROWSERS"

echo "==> 7/7 systemd units (service + weekly yt-dlp update timer)"
install -m 0644 "$PROJ/systemd/mix.service" /etc/systemd/system/mix.service
install -m 0644 "$PROJ/systemd/mix-update-ytdlp.service" /etc/systemd/system/mix-update-ytdlp.service
install -m 0644 "$PROJ/systemd/mix-update-ytdlp.timer" /etc/systemd/system/mix-update-ytdlp.timer
systemctl daemon-reload
systemctl enable --now mix.service
systemctl enable --now mix-update-ytdlp.timer

echo
echo "setup complete. service: $(systemctl is-active mix.service); next yt-dlp update:"
systemctl list-timers mix-update-ytdlp.timer --no-pager | sed -n 2p
