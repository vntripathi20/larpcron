"""Instagram publishing via instagrapi (unofficial private API, logs in with username + password).

Heads up: this is against Instagram's Terms of Use and can get an account challenged or banned.
Use a throwaway/secondary account, keep posting frequency human-ish, and keep dry_run on until
you've looked at the output.

Instagram retires old app versions now and then, and instagrapi then fails with "Your version of
Instagram is out of date". When that happens, post_reel and post_photo upgrade instagrapi (and only
instagrapi) once, forget the saved session, and try the login again. They never retry twice.

Login order in _client: the saved session (checked with a cheap call), then the sessionid cookie
(instagram_sessionid), then username and password. Instagram can reject password logins outright
when the app version is too old, and a cookie from a logged-in browser gets around that.
"""
import importlib
import re
import subprocess
import sys
import time
from pathlib import Path

from .config import DATA

SESSION_FILE = DATA / "instagram_session.json"
HEAL_STAMP = DATA / "instagram_heal.stamp"   # its mtime = when the last self-heal ran
HEAL_COOLDOWN = 6 * 3600                     # don't run pip again for the same problem within 6 hours
UPDATE_LOG = DATA / "update.log"

# Wording Instagram and instagrapi use when the app version is too old.
OUTDATED = re.compile(
    r"out of date|outdated|update (?:your |the )?(?:instagram )?app|update_required|"
    r"please update|upgrade (?:your |the )?(?:instagram )?app|version of instagram",
    re.IGNORECASE,
)


def _log(msg: str) -> None:
    from . import job  # imported late: job imports this module

    job.log(msg)
    try:
        DATA.mkdir(parents=True, exist_ok=True)
        with open(UPDATE_LOG, "a", encoding="utf-8") as fh:
            fh.write(f"{time.strftime('%Y-%m-%d %H:%M:%S')} {msg}\n")
    except OSError:
        pass


def looks_outdated(exc: BaseException) -> bool:
    return bool(OUTDATED.search(f"{type(exc).__name__}: {exc}"))


def _instagrapi_version() -> str:
    from importlib import metadata

    try:
        return metadata.version("instagrapi")
    except metadata.PackageNotFoundError:
        return "unknown"


def _upgrade_instagrapi() -> bool:
    """pip install -U instagrapi, nothing else. True when pip succeeded."""
    try:
        r = subprocess.run([sys.executable, "-m", "pip", "install", "--disable-pip-version-check", "-q",
                            "-U", "instagrapi"], capture_output=True, text=True, timeout=600)
    except (OSError, subprocess.SubprocessError) as e:
        _log(f"Couldn't run pip to upgrade instagrapi: {e}")
        return False
    if r.returncode != 0:
        tail = " | ".join((r.stderr or r.stdout).strip().splitlines()[-3:])
        _log(f"pip could not upgrade instagrapi: {tail}")
        return False
    return True


def _reload_instagrapi() -> None:
    """Drop the old instagrapi from memory so the next import loads the upgraded files."""
    for name in [m for m in sys.modules if m == "instagrapi" or m.startswith("instagrapi.")]:
        del sys.modules[name]
    importlib.invalidate_caches()


def _heal_allowed() -> bool:
    try:
        return time.time() - HEAL_STAMP.stat().st_mtime > HEAL_COOLDOWN
    except OSError:
        return True


def _self_heal(error: BaseException) -> bool:
    """Upgrade instagrapi, reload it and forget the saved session. True when a retry makes sense."""
    if not _heal_allowed():
        _log(f"Instagram says the app version is out of date ({error}), but instagrapi was already upgraded "
             "in the last 6 hours. Set 'Instagram app version' in the Accounts tab if this keeps happening.")
        return False
    old = _instagrapi_version()
    _log(f"Instagram says the app version is out of date ({error}). Upgrading instagrapi (now {old}).")
    try:
        DATA.mkdir(parents=True, exist_ok=True)
        HEAL_STAMP.write_text(time.strftime("%Y-%m-%d %H:%M:%S"), encoding="utf-8")
    except OSError:
        pass
    if not _upgrade_instagrapi():
        return False
    _reload_instagrapi()
    new = _instagrapi_version()
    _log(f"instagrapi {old} -> {new}." + ("" if new != old else " Already the newest version."))
    try:
        SESSION_FILE.unlink(missing_ok=True)
    except OSError:
        pass
    _log("Deleted the saved Instagram session. Logging in again.")
    return True


APP_VERSION_KEYS = ("app_version", "version_code", "bloks_versioning_id")

NEEDS_SESSIONID = ("Instagram rejected password login. Paste a sessionid cookie from a logged-in browser "
                   "in Settings.")
SESSIONID_REJECTED = ("Instagram rejected the login even with the sessionid cookie. It has probably expired. "
                      "Paste a fresh sessionid cookie from a logged-in browser in Settings.")


def app_version_override(cfg: dict) -> dict:
    """The non-empty app_version / version_code / bloks_versioning_id entries from the config."""
    raw = cfg.get("instagram_app_version") or {}
    if isinstance(raw, str):  # an older config stored just the app version
        raw = {"app_version": raw}
    if not isinstance(raw, dict):
        return {}
    return {k: str(raw[k]).strip() for k in APP_VERSION_KEYS if str(raw.get(k, "")).strip()}


def _apply_app_version(cl, override: dict) -> None:
    """Make the client report a different Instagram app (the 'instagram_app_version' setting)."""
    if not override:
        return
    cl.device_settings.update(override)
    cl.set_user_agent()


def _session_works(cl) -> bool:
    """A cheap logged-in call. Any failure means the saved session can't be used."""
    try:
        cl.account_info()
        return True
    except Exception:
        return False


def _client(cfg: dict):
    """A logged-in client. Order: saved session, then the sessionid cookie, then username and password."""
    from instagrapi import Client

    override = app_version_override(cfg)
    sessionid = (cfg.get("instagram_sessionid") or "").strip()
    user, pw = cfg.get("instagram_username", ""), cfg.get("instagram_password", "")
    if not sessionid and (not user or not pw):
        raise RuntimeError("Instagram is not set up. Add a sessionid cookie, or a username and password, in Settings.")

    def fresh():
        cl = Client()
        cl.delay_range = [1, 3]
        return cl

    # 1. The saved session.
    if SESSION_FILE.exists():
        cl = fresh()
        try:
            cl.load_settings(SESSION_FILE)
            _apply_app_version(cl, override)
            if _session_works(cl):
                return cl
        except Exception:
            pass

    # 2. The sessionid cookie from a browser that is logged in.
    sessionid_error = None
    if sessionid:
        cl = fresh()
        try:
            _apply_app_version(cl, override)
            cl.login_by_sessionid(sessionid)
            DATA.mkdir(parents=True, exist_ok=True)
            cl.dump_settings(SESSION_FILE)
            return cl
        except Exception as e:
            sessionid_error = e
            _log("Logging in with the sessionid cookie failed: " + type(e).__name__)

    # 3. Username and password, the last resort.
    if not user or not pw:
        raise RuntimeError(SESSIONID_REJECTED) if sessionid_error is not None else RuntimeError(NEEDS_SESSIONID)
    cl = fresh()
    if SESSION_FILE.exists():
        try:
            cl.load_settings(SESSION_FILE)
        except Exception:
            pass
    _apply_app_version(cl, override)
    code = ""
    seed = cfg.get("instagram_2fa_seed", "").replace(" ", "")
    if seed:
        code = cl.totp_generate_code(seed)
    cl.login(user, pw, verification_code=code)
    DATA.mkdir(parents=True, exist_ok=True)
    cl.dump_settings(SESSION_FILE)
    return cl


def _with_retry(cfg: dict, action):
    """Log in and run action(client). If Instagram says the app is out of date, heal once and try again."""
    sessionid = bool((cfg.get("instagram_sessionid") or "").strip())
    try:
        try:
            return action(_client(cfg))
        except Exception as e:
            if not looks_outdated(e) or not _self_heal(e):
                raise
        # Second and last attempt. Whatever it raises goes to the caller.
        return action(_client(cfg))
    except Exception as e:
        if looks_outdated(e):
            raise RuntimeError(SESSIONID_REJECTED if sessionid else NEEDS_SESSIONID) from e
        raise


def post_reel(cfg: dict, video: Path, caption: str) -> str:
    media = _with_retry(cfg, lambda cl: cl.clip_upload(video, caption))
    return str(media.code)


def post_photo(cfg: dict, image: Path, caption: str) -> str:
    media = _with_retry(cfg, lambda cl: cl.photo_upload(image, caption))
    return str(media.code)
