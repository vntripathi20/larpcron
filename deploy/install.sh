#!/usr/bin/env bash
# One-command installer for Debian, Ubuntu and Raspberry Pi OS (Bookworm or newer).
#
#   bash deploy/install.sh                     asks whether to turn on automatic updates
#   bash deploy/install.sh --auto-update       turns them on without asking
#   bash deploy/install.sh --no-auto-update    leaves them off without asking (for unattended installs)
#   bash deploy/install.sh --dry-run           prints what it would do and what it would write, changes nothing
#
# Run it as your normal user from inside the cloned repo. It uses sudo for the system parts.
# Running it again is safe: it refreshes the venv, the service files and the sudo rule.
#
# What it sets up:
#   - apt packages: python3-venv, python3-pip, git, ffmpeg, fonts-dejavu-core, nodejs
#   - a virtualenv in .venv with the requirements installed
#   - the larpcron systemd service (starts at boot)
#   - larpcron-update.service and larpcron-update.timer, a daily check around 04:00 (only if you say yes)
#   - larpcron-update-now.service, which the panel's "Update now" button starts
#   - /etc/sudoers.d/larpcron, which lets your user run exactly two commands without a password:
#     "systemctl restart larpcron" and "systemctl start --no-block larpcron-update-now.service"

set -euo pipefail

AUTO="ask"
DRY=0

say() { printf '\n==> %s\n' "$*"; }
die() { printf 'Error: %s\n' "$*" >&2; exit 1; }
run() { if [ "$DRY" -eq 1 ]; then printf '[dry run] %s\n' "$*"; else "$@"; fi; }

for arg in "$@"; do
  case "$arg" in
    --auto-update) AUTO="yes" ;;
    --no-auto-update) AUTO="no" ;;
    --dry-run) DRY=1 ;;
    -h|--help) sed -n '2,20p' "${BASH_SOURCE[0]}" | sed 's/^# \{0,1\}//'; exit 0 ;;
    *) die "Unknown option: $arg (try --help)" ;;
  esac
done

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
APP_USER="$(id -un)"
[[ "$REPO" =~ ^[A-Za-z0-9_./-]+$ ]] || die "The install path ($REPO) has spaces or odd characters. Move the folder somewhere plain, like ~/LarpCron."
[[ "$APP_USER" =~ ^[a-z_][a-z0-9_-]*$ ]] || die "Unexpected user name: $APP_USER"
SYSTEMCTL="$(command -v systemctl || true)"
[ -n "$SYSTEMCTL" ] || SYSTEMCTL="/usr/bin/systemctl"

# The unit files in deploy/ are written for user "pi" in /home/pi/LarpCron. Swap in this machine's values.
render_unit() { sed "s|/home/pi/LarpCron|$REPO|g; s|User=pi|User=$APP_USER|" "$REPO/deploy/$1"; }

install_unit() {
  if [ "$DRY" -eq 1 ]; then
    printf '[dry run] would write /etc/systemd/system/%s:\n' "$1"
    render_unit "$1" | sed 's/^/    | /'
  else
    render_unit "$1" | sudo tee "/etc/systemd/system/$1" >/dev/null
  fi
}

sudoers_text() {
  printf '# Installed by LarpCron deploy/install.sh. Lets %s restart LarpCron and start its manual update, nothing else.\n' "$APP_USER"
  printf '%s ALL=(root) NOPASSWD: %s restart larpcron, %s start --no-block larpcron-update-now.service\n' \
    "$APP_USER" "$SYSTEMCTL" "$SYSTEMCTL"
}

set_auto_update_setting() {  # set_auto_update_setting true|false  -> data/config.json (other settings kept)
  if [ "$DRY" -eq 1 ]; then
    printf '[dry run] would set auto_update=%s in %s/data/config.json\n' "$1" "$REPO"
    return
  fi
  "$REPO/.venv/bin/python" - "$REPO/data/config.json" "$1" <<'PYEOF'
import json, os, sys
path, value = sys.argv[1], sys.argv[2] == "true"
os.makedirs(os.path.dirname(path), exist_ok=True)
try:
    with open(path) as fh:
        cfg = json.load(fh)
except Exception:
    cfg = {}
cfg["auto_update"] = value
tmp = path + ".tmp"
with open(tmp, "w") as fh:
    json.dump(cfg, fh, indent=2)
os.replace(tmp, path)
PYEOF
}

# ---- checks ---------------------------------------------------------------------------------------
if [ "$DRY" -eq 0 ]; then
  [ "$(id -u)" -ne 0 ] || die "Run this as your normal user, not root. It asks for sudo when it needs it."
  command -v apt-get >/dev/null || die "This installer is for Debian, Ubuntu and Raspberry Pi OS. See the manual steps in the README."
  [ -d /run/systemd/system ] || die "systemd isn't running here. See the manual steps in the README."
  command -v sudo >/dev/null || die "sudo is needed. Install it, or use the manual steps in the README."
  [ -f "$REPO/requirements.txt" ] || die "Run this from inside the LarpCron folder (couldn't find requirements.txt)."
  sudo -v
else
  say "Dry run: nothing below is executed. Repo: $REPO, user: $APP_USER"
fi

# ---- packages and virtualenv ----------------------------------------------------------------------
say "Installing system packages"
run sudo apt-get update
run sudo apt-get install -y python3 python3-venv python3-pip git ffmpeg fonts-dejavu-core nodejs

if [ "$DRY" -eq 0 ]; then
  python3 -c 'import sys; sys.exit(0 if sys.version_info >= (3, 10) else 1)' \
    || die "LarpCron needs Python 3.10 or newer, and this has $(python3 --version 2>&1). Use Raspberry Pi OS Bookworm or newer."
fi

say "Setting up the Python environment"
if [ "$DRY" -eq 1 ] || [ ! -x "$REPO/.venv/bin/python" ]; then run python3 -m venv "$REPO/.venv"; fi
run "$REPO/.venv/bin/pip" install --disable-pip-version-check -r "$REPO/requirements.txt"

# ---- systemd ----------------------------------------------------------------------------------------
say "Installing the services"
install_unit larpcron.service
install_unit larpcron-update.service
install_unit larpcron-update-now.service
install_unit larpcron-update.timer

say "Allowing $APP_USER to restart LarpCron and start updates (and nothing else)"
if [ "$DRY" -eq 1 ]; then
  printf '[dry run] would write /etc/sudoers.d/larpcron (after checking it with visudo -cf):\n'
  sudoers_text | sed 's/^/    | /'
else
  tmp="$(mktemp)"
  sudoers_text >"$tmp"
  sudo visudo -cf "$tmp" >/dev/null || { rm -f "$tmp"; die "The sudoers rule failed its syntax check, so it was not installed."; }
  sudo install -m 0440 -o root -g root "$tmp" /etc/sudoers.d/larpcron
  rm -f "$tmp"
fi

run sudo systemctl daemon-reload
run sudo systemctl enable --now larpcron

# ---- automatic updates ------------------------------------------------------------------------------
if [ "$AUTO" = "ask" ]; then
  if [ "$DRY" -eq 0 ] && [ -t 0 ]; then
    printf '\nEnable automatic updates? [Y/n] '
    read -r answer || answer=""
    case "${answer:-Y}" in [Nn]*) AUTO="no" ;; *) AUTO="yes" ;; esac
  else
    AUTO="yes"   # no one to ask: take the default. Use --no-auto-update to opt out when unattended.
  fi
fi

if [ "$AUTO" = "yes" ]; then
  say "Turning on automatic updates (daily around 04:00)"
  set_auto_update_setting true
  run sudo systemctl enable --now larpcron-update.timer
else
  say "Leaving automatic updates off"
  set_auto_update_setting false
  run sudo systemctl disable --now larpcron-update.timer 2>/dev/null || true
fi

# ---- done -------------------------------------------------------------------------------------------
ip="$(hostname -I 2>/dev/null | awk '{print $1}')"
say "Done"
cat <<EOF
Open the panel from another device on the same network:
  http://${ip:-<this-device's-ip>}:8080     (or http://$(hostname).local:8080)
Log in with user admin and password larp, then change that password in the Accounts tab.

Automatic updates: $AUTO. You can change that any time in the panel's Updates tab.
Logs:  journalctl -u larpcron -f      Update log: $REPO/data/update.log
EOF
