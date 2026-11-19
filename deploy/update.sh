#!/usr/bin/env bash
# LarpCron updater.
#
# It runs from larpcron-update.service (the daily timer) and from the panel's "Update now" button
# (larpcron-update-now.service, which passes --now). You can run it by hand too:
#
#   bash deploy/update.sh          # respects the auto_update setting
#   bash deploy/update.sh --now    # updates even when auto_update is off
#
# What it does: fetch from origin. If origin has new commits that fast-forward cleanly, pull them,
# reinstall requirements and yt-dlp, restart the service and check that it came back. If pip or the
# restart fails, it goes back to the commit it started from.
#
# Safety rules:
#   - It only pulls from the remote named "origin", with --ff-only. Nothing else is fetched or run
#     except this script itself, from the checkout.
#   - It won't start if tracked files have local changes, so a rollback can never throw away your edits.
#   - It won't start while a reel is rendering or posting (see data/run.lock), and it holds
#     data/updating while it works so a new run can't start halfway through.
#   - It never edits or deletes your data/. The only files it writes there are its own:
#     update.log, update_status.json, updating and update.lock.d.
#
# Test hooks, not needed normally: LARPCRON_RESTART_CMD, LARPCRON_HEALTH_CMD, LARPCRON_HEALTH_WAIT.

# Everything lives in functions and the last line is the only code that runs. Bash has therefore read
# the whole script before `git pull` gets the chance to replace this file.

set -u

ROOT=""; DATA=""; LOG=""; STATUS=""; PY=""; PIP=""; MODE="auto"
PREV=""; PREV_SHORT=""; NEW_SHORT=""; BRANCH=""; SYSTEMCTL=""

ts() { date '+%Y-%m-%d %H:%M:%S'; }

log() { printf '%s %s\n' "$(ts)" "$*" | tee -a "$LOG"; }

# write_status RESULT MESSAGE [FROM] [TO]  ->  data/update_status.json (the panel reads this)
write_status() {
  "$PY" - "$STATUS" "$MODE" "$1" "$2" "${3:-}" "${4:-}" <<'PYEOF' 2>/dev/null || true
import json, os, sys, time
path, mode, result, message, old, new = sys.argv[1:7]
data = {"time": time.strftime("%Y-%m-%d %H:%M:%S"), "epoch": int(time.time()), "mode": mode,
        "result": result, "message": message, "from": old, "to": new}
tmp = path + ".tmp"
with open(tmp, "w") as fh:
    json.dump(data, fh)
os.replace(tmp, path)
PYEOF
}

auto_update_enabled() {
  local cfg="$DATA/config.json" v
  [ -f "$cfg" ] || return 0
  v=$("$PY" -c 'import json, sys
try:
    print("0" if json.load(open(sys.argv[1])).get("auto_update") is False else "1")
except Exception:
    print("1")' "$cfg" 2>/dev/null) || v=1
  [ "$v" != "0" ]
}

# busy: true when data/run.lock says a render or post is in progress.
# The file holds "pid=" and "since="; a dead pid or one older than 30 minutes is a leftover, not a run.
busy() {
  local f="$DATA/run.lock" pid since now
  [ -f "$f" ] || return 1
  pid=$(sed -n 's/^pid=//p' "$f" | head -n 1)
  since=$(sed -n 's/^since=//p' "$f" | head -n 1)
  case "$pid" in ''|*[!0-9]*) return 1 ;; esac
  case "$since" in ''|*[!0-9]*) return 1 ;; esac
  now=$(date +%s)
  [ $((now - since)) -gt 1800 ] && return 1
  if kill -0 "$pid" 2>/dev/null || [ -d "/proc/$pid" ]; then return 0; fi
  return 1
}

acquire_lock() {
  local d="$DATA/update.lock.d"
  if ! mkdir "$d" 2>/dev/null; then
    # a lock older than two hours belongs to an update that died
    if [ -n "$(find "$d" -maxdepth 0 -mmin +120 2>/dev/null)" ]; then
      rmdir "$d" 2>/dev/null && mkdir "$d" 2>/dev/null || return 1
    else
      return 1
    fi
  fi
  return 0
}

cleanup() {
  rm -f "$DATA/updating" 2>/dev/null
  rmdir "$DATA/update.lock.d" 2>/dev/null
}

has_service() {
  [ -n "${LARPCRON_RESTART_CMD:-}" ] && return 0
  [ -n "$SYSTEMCTL" ] && "$SYSTEMCTL" cat larpcron >/dev/null 2>&1
}

restart_service() {
  if [ -n "${LARPCRON_RESTART_CMD:-}" ]; then
    bash -c "$LARPCRON_RESTART_CMD"
  elif [ "$(id -u)" -eq 0 ]; then
    "$SYSTEMCTL" restart larpcron
  else
    sudo -n "$SYSTEMCTL" restart larpcron
  fi
}

# healthy: wait a few seconds after a restart, then ask whether the service is really up
healthy() {
  sleep "${LARPCRON_HEALTH_WAIT:-8}"
  if [ -n "${LARPCRON_HEALTH_CMD:-}" ]; then
    bash -c "$LARPCRON_HEALTH_CMD"
  else
    "$SYSTEMCTL" is-active --quiet larpcron
  fi
}

pip_install() {  # pip_install ARGS...   (output goes to the log; the last lines are shown on failure)
  local out rc
  out=$(mktemp) || return 1
  "$PIP" install --disable-pip-version-check -q "$@" >"$out" 2>&1
  rc=$?
  if [ $rc -ne 0 ]; then
    tail -n 15 "$out" | sed "s/^/    pip: /" >>"$LOG"
  fi
  rm -f "$out"
  return $rc
}

# rollback REASON: go back to the commit we started from, reinstall, restart
rollback() {
  log "Rolling back to $PREV_SHORT because: $1"
  if ! git reset --hard --quiet "$PREV" >>"$LOG" 2>&1; then
    log "Rollback failed: could not reset to $PREV_SHORT. This needs a look by hand."
    write_status failed "$1. The rollback to $PREV_SHORT failed too, so check the checkout by hand." "$PREV_SHORT" "$NEW_SHORT"
    return 1
  fi
  pip_install -r requirements.txt || log "Reinstalling the old requirements failed. Check the log above."
  if has_service; then
    if restart_service >>"$LOG" 2>&1 && healthy; then
      log "Rolled back to $PREV_SHORT and the service is running again."
    else
      log "Rolled back to $PREV_SHORT, but the service did not come back. Run: sudo systemctl status larpcron"
    fi
  fi
  write_status rolled-back "The update failed ($1), so it went back to $PREV_SHORT." "$PREV_SHORT" "$NEW_SHORT"
  return 1
}

main() {
  local want_now=0 arg new_count
  for arg in "$@"; do
    case "$arg" in
      --now) want_now=1; MODE="manual" ;;
      -h|--help) sed -n '2,12p' "${BASH_SOURCE[0]}" | sed 's/^# \{0,1\}//'; return 0 ;;
      *) echo "Unknown option: $arg (try --help)" >&2; return 2 ;;
    esac
  done

  ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)" || return 2
  cd "$ROOT" || return 2
  DATA="$ROOT/data"; LOG="$DATA/update.log"; STATUS="$DATA/update_status.json"
  mkdir -p "$DATA" || return 2
  export GIT_TERMINAL_PROMPT=0
  SYSTEMCTL="$(command -v systemctl || true)"
  if [ -x "$ROOT/.venv/bin/python" ]; then PY="$ROOT/.venv/bin/python"; else PY="$(command -v python3 || true)"; fi
  PIP="$ROOT/.venv/bin/pip"

  if ! acquire_lock; then
    log "Another update is already running, so this one is skipping."
    return 0
  fi
  trap cleanup EXIT

  if [ "$want_now" -eq 0 ] && ! auto_update_enabled; then
    log "Automatic updates are switched off in the panel, so nothing to do."
    return 0
  fi

  if ! git rev-parse --is-inside-work-tree >/dev/null 2>&1; then
    log "This folder isn't a git checkout, so it can't update itself."
    write_status failed "This install isn't a git checkout, so it can't update itself."
    return 1
  fi
  if [ ! -x "$PIP" ]; then
    log "No virtualenv found at .venv, so there's nowhere to install requirements. Run deploy/install.sh."
    write_status failed "No virtualenv at .venv. Run deploy/install.sh."
    return 1
  fi
  BRANCH="$(git symbolic-ref --quiet --short HEAD || true)"
  if [ -z "$BRANCH" ]; then
    log "The checkout isn't on a branch (detached HEAD), so it won't update."
    write_status skipped "The checkout isn't on a branch, so it wasn't updated."
    return 0
  fi
  if ! git remote get-url origin >/dev/null 2>&1; then
    log "There's no remote called origin, so there's nothing to update from."
    write_status failed "This checkout has no remote called origin."
    return 1
  fi
  if [ -n "$(git status --porcelain --untracked-files=no)" ]; then
    log "Skipped: tracked files have local changes, and an update (or a rollback) would overwrite them."
    write_status skipped "Some files in the install folder were changed by hand, so the update was skipped."
    return 0
  fi

  PREV="$(git rev-parse HEAD)"; PREV_SHORT="$(git rev-parse --short HEAD)"

  if ! git fetch --quiet origin "+refs/heads/$BRANCH:refs/remotes/origin/$BRANCH" >>"$LOG" 2>&1; then
    log "Couldn't reach origin (offline?). Will try again next run."
    write_status skipped "Couldn't reach GitHub. It will try again next time."
    return 0
  fi
  local remote
  remote="$(git rev-parse "origin/$BRANCH")"
  if [ "$remote" = "$PREV" ]; then
    log "Already up to date at $PREV_SHORT."
    write_status up-to-date "" "$PREV_SHORT" "$PREV_SHORT"
    return 0
  fi
  if git merge-base --is-ancestor "$remote" "$PREV"; then
    log "This checkout is ahead of origin, so there's nothing to pull."
    write_status up-to-date "This copy is ahead of GitHub." "$PREV_SHORT" "$PREV_SHORT"
    return 0
  fi
  if ! git merge-base --is-ancestor "$PREV" "$remote"; then
    log "Skipped: this checkout and origin/$BRANCH have diverged, so a fast-forward isn't possible."
    write_status skipped "This copy has commits that GitHub doesn't, so it can't fast-forward." "$PREV_SHORT"
    return 0
  fi
  new_count="$(git rev-list --count "$PREV..$remote")"

  # An update is going to happen. Put the marker down first, then look for a running reel.
  touch "$DATA/updating"
  if busy; then
    log "Skipped: a reel is rendering or posting. Will try again next run."
    write_status skipped "A reel was being made, so the update waits for the next run." "$PREV_SHORT"
    return 0
  fi

  log "Updating $PREV_SHORT to origin/$BRANCH ($new_count new commit(s)) from $(git remote get-url origin | sed 's#://[^/@]*@#://#')"
  if ! git pull --ff-only --quiet origin "$BRANCH" >>"$LOG" 2>&1; then
    log "git pull failed. Nothing was changed."
    NEW_SHORT="$(git rev-parse --short HEAD)"
    [ "$(git rev-parse HEAD)" = "$PREV" ] || { rollback "git pull failed"; return 1; }
    write_status failed "git pull failed, so nothing was changed." "$PREV_SHORT"
    return 1
  fi
  NEW_SHORT="$(git rev-parse --short HEAD)"

  if ! pip_install -r requirements.txt; then rollback "pip install -r requirements.txt failed"; return 1; fi
  if ! pip_install -U yt-dlp; then rollback "pip install -U yt-dlp failed"; return 1; fi

  if has_service; then
    if ! restart_service >>"$LOG" 2>&1; then rollback "restarting the service failed"; return 1; fi
    if ! healthy; then rollback "the service was not running after the restart"; return 1; fi
    log "Updated $PREV_SHORT to $NEW_SHORT and restarted the service."
    write_status updated "" "$PREV_SHORT" "$NEW_SHORT"
  else
    log "Updated $PREV_SHORT to $NEW_SHORT. There's no systemd service here, so restart LarpCron yourself."
    write_status updated "Restart LarpCron to use the new version." "$PREV_SHORT" "$NEW_SHORT"
  fi
  return 0
}

main "$@"; exit $?
