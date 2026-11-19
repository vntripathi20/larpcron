#!/usr/bin/env bash
# Installs the browser that TikTok and YouTube Shorts posting needs (Playwright's Chromium).
#
#   bash deploy/install-browser.sh
#
# Run it once, after you turn on TikTok or YouTube in the panel's Platforms tab. Nothing else in LarpCron
# needs it, so it isn't part of deploy/install.sh and nothing downloads it at startup.
#
# It downloads a few hundred MB and installs system libraries with apt, so it asks for sudo.

set -u

die() { printf 'Error: %s\n' "$*" >&2; exit 1; }
say() { printf '\n==> %s\n' "$*"; }

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)" || exit 1
PY="$ROOT/.venv/bin/python"
[ -x "$PY" ] || die "No virtualenv at $ROOT/.venv. Run deploy/install.sh (or the manual install steps in the README) first."

if ! "$PY" -c 'import playwright' 2>/dev/null; then
  say "Installing Playwright into the virtualenv"
  "$PY" -m pip install --disable-pip-version-check -r "$ROOT/requirements.txt" || die "pip failed."
fi

case "$(uname -m)" in
  armv7l|armv6l|i?86)
    die "Playwright has no Chromium for 32-bit systems ($(uname -m)). Install 64-bit Raspberry Pi OS, or leave TikTok and YouTube off." ;;
esac

MEM_MB="$(awk '/MemTotal/ {printf "%d", $2/1024}' /proc/meminfo 2>/dev/null || echo 0)"
if [ "${MEM_MB:-0}" -gt 0 ] && [ "$MEM_MB" -lt 1500 ]; then
  printf '\nNote: this machine has about %s MB of RAM. Chromium will run, but slowly, and an upload can take\n' "$MEM_MB"
  printf 'several minutes. A Pi 4 or 5 is a lot better for this. Adding swap helps if uploads get killed.\n'
fi

say "Downloading Chromium"
"$PY" -m playwright install chromium || die "Couldn't download Chromium."

say "Installing the system libraries Chromium needs (asks for sudo)"
if [ "$(id -u)" -eq 0 ]; then
  "$PY" -m playwright install-deps chromium || die "playwright install-deps failed."
else
  command -v sudo >/dev/null || die "sudo is needed for the system libraries. Run this script as root instead."
  sudo "$PY" -m playwright install-deps chromium || die "playwright install-deps failed."
fi

say "Done"
echo "Chromium is ready. Turn TikTok or YouTube on in the Platforms tab, paste your cookies, and press Test upload."
