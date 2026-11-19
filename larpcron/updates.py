"""Auto-update support for the panel.

deploy/update.sh does the actual updating. This module only reads what it leaves in data/ and asks
systemd to start a manual update. The manual update runs as its own unit (larpcron-update-now.service)
so it survives the restart of the LarpCron service that it triggers.
"""
import json
import os
import re
import shutil
import subprocess
import time

from . import config, job

STATUS_FILE = config.DATA / "update_status.json"
DEPS_STATUS_FILE = config.DATA / "deps_status.json"   # result of the instagrapi + yt-dlp upgrade job
PACKAGES = ("instagrapi", "yt-dlp")                   # the only packages the updater upgrades
LOG_FILE = config.DATA / "update.log"
UPDATING_FILE = config.DATA / "updating"
MARKER_MAX_AGE = 20 * 60
NOW_UNIT = "larpcron-update-now.service"
TIMER_UNIT = "larpcron-update.timer"

RESULT_WORDS = {
    "updated": "Updated",
    "up-to-date": "Already up to date",
    "skipped": "Skipped",
    "failed": "Failed",
    "rolled-back": "Failed, so it went back to the old version",
}


def _run(cmd: list[str], timeout: int = 5) -> subprocess.CompletedProcess | None:
    try:
        return subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    except (OSError, subprocess.SubprocessError):
        return None


def _git(*args: str) -> str:
    r = _run(["git", "-C", str(config.ROOT), *args])
    return r.stdout.strip() if r and r.returncode == 0 else ""


def running() -> bool:
    """True while update.sh is replacing the code (it keeps data/updating around for that long)."""
    try:
        return time.time() - UPDATING_FILE.stat().st_mtime < MARKER_MAX_AGE
    except OSError:
        return False


def last_result() -> dict:
    try:
        return json.loads(STATUS_FILE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def describe_last(last: dict | None = None) -> str:
    last = last_result() if last is None else last
    if not last:
        return "No update has run yet."
    word = RESULT_WORDS.get(last.get("result", ""), last.get("result", "Ran"))
    text = f"{word} on {last.get('time', 'an unknown date')}."
    if last.get("result") == "updated" and last.get("from") and last.get("to"):
        text = f"{word} from {last['from']} to {last['to']} on {last.get('time', 'an unknown date')}."
    if last.get("message") and last.get("result") != "updated":
        text += " " + last["message"]
    return text


def installed_versions() -> dict:
    """Versions of the upgradeable packages, as this running panel sees them."""
    from importlib import metadata

    out = {}
    for name in PACKAGES:
        try:
            out[name] = metadata.version(name)
        except metadata.PackageNotFoundError:
            out[name] = ""
    return out


def deps_last() -> dict:
    try:
        return json.loads(DEPS_STATUS_FILE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def describe_deps(last: dict | None = None) -> str:
    last = deps_last() if last is None else last
    if not last:
        return "Not checked yet."
    when = last.get("time", "an unknown date")
    result, message = last.get("result", ""), last.get("message", "")
    if result == "updated":
        return f"Upgraded on {when}: {message}."
    if result == "up-to-date":
        return f"Checked on {when}. Nothing newer was available."
    word = RESULT_WORDS.get(result, result or "Ran")
    return f"{word} on {when}." + (" " + message if message else "")


def version() -> dict:
    out = _git("log", "-1", "--format=%h|%cs|%s")
    if not out:
        return {"commit": "", "date": "", "subject": ""}
    commit, date, subject = (out.split("|", 2) + ["", ""])[:3]
    return {"commit": commit, "date": date, "subject": subject}


def origin_url() -> str:
    url = _git("remote", "get-url", "origin")
    return re.sub(r"://[^/@]+@", "://", url)  # never show a token that someone put in the URL


def _systemctl() -> str | None:
    return shutil.which("systemctl")


def can_update_now() -> bool:
    ctl = _systemctl()
    if not ctl:
        return False
    r = _run([ctl, "cat", NOW_UNIT])
    return bool(r and r.returncode == 0)


def timer_enabled() -> bool:
    ctl = _systemctl()
    if not ctl:
        return False
    r = _run([ctl, "is-enabled", "--quiet", TIMER_UNIT])
    return bool(r and r.returncode == 0)


def log_tail(lines: int = 15) -> str:
    try:
        return "\n".join(LOG_FILE.read_text(encoding="utf-8", errors="replace").splitlines()[-lines:])
    except OSError:
        return ""


def live() -> dict:
    """Cheap and safe to poll: only reads small files."""
    last = last_result()
    deps = deps_last()
    return {"running": running(), "last": last, "last_text": describe_last(last),
            "deps_last": deps, "deps_text": describe_deps(deps)}


def info() -> dict:
    """Everything the Updates tab shows when the page loads."""
    return {**live(), "version": version(), "origin": origin_url(), "can_update": can_update_now(),
            "timer_enabled": timer_enabled(), "log": log_tail(), "packages": installed_versions()}


def request_update() -> tuple[bool, str]:
    """Ask systemd to start update.sh --now. Returns (started, message for the person)."""
    if running():
        return False, "An update is already running."
    if job.STATE["busy"]:
        return False, "A reel is being made right now. Try again in a minute."
    ctl = _systemctl()
    if not ctl or not can_update_now():
        return False, ("Updating from the panel needs the setup from deploy/install.sh. "
                       "On this install, run git pull and restart LarpCron yourself.")
    cmd = [ctl, "start", "--no-block", NOW_UNIT]
    if hasattr(os, "geteuid") and os.geteuid() != 0:
        cmd = ["sudo", "-n", *cmd]
    r = _run(cmd, timeout=20)
    if r is None or r.returncode != 0:
        detail = (r.stderr.strip().splitlines() or [""])[0] if r else ""
        return False, ("Couldn't start the update. If it says sudo wants a password, run deploy/install.sh again. "
                       + detail).strip()
    return True, "Update started. If there's a new version, the panel restarts by itself."
