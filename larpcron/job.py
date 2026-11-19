"""The pipeline: pick footage + quote -> render -> (optionally) post. Keeps a rolling log and history."""
import json
import os
import random
import threading
import time
from collections import deque

from . import config, media, platforms, quotes, reel
from .platforms import status as platform_status

_run_lock = threading.Lock()
LOG: deque = deque(maxlen=200)
STATE = {"busy": False, "last_result": None}
HISTORY_FILE = config.DATA / "history.json"

# Files shared with deploy/update.sh, which is a separate process and can't see the in-memory lock above.
RUN_FILE = config.DATA / "run.lock"      # present while a render/post is running: "pid=..." and "since=..."
UPDATING_FILE = config.DATA / "updating"  # present while update.sh is replacing the code
UPDATE_MARKER_MAX_AGE = 20 * 60           # ignore a marker left behind by an update that died


def log(msg: str) -> None:
    LOG.append(f"{time.strftime('%Y-%m-%d %H:%M:%S')}  {msg}")
    print(msg, flush=True)


def history() -> list:
    try:
        return json.loads(HISTORY_FILE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []


def _add_history(entry: dict) -> None:
    items = (history() + [entry])[-100:]
    HISTORY_FILE.write_text(json.dumps(items, indent=2), encoding="utf-8")


def _write_run_file() -> None:
    try:
        RUN_FILE.write_text("pid=%d\nsince=%d\n" % (os.getpid(), time.time()), encoding="utf-8")
    except OSError:
        pass


def _remove_run_file() -> None:
    try:
        RUN_FILE.unlink(missing_ok=True)
    except OSError:
        pass


def update_in_progress() -> bool:
    try:
        return time.time() - UPDATING_FILE.stat().st_mtime < UPDATE_MARKER_MAX_AGE
    except OSError:
        return False


PAUSE_BETWEEN_PLATFORMS = (30, 120)  # seconds, random. The Pi 3B has about 1 GB of RAM, so uploads go one at a time.


def _pause_between_platforms() -> None:
    wait = random.uniform(*PAUSE_BETWEEN_PLATFORMS)
    log(f"Waiting {int(wait)} seconds before the next platform.")
    time.sleep(wait)


def post_everywhere(cfg: dict, kind: str, out, quote: str, author: str, caption: str) -> dict:
    """Post one file to every enabled platform, one after another. One platform failing never stops the next.

    Returns {platform key: {"ok": bool, "text": result or error}} for the platforms that were tried."""
    results: dict = {}
    for p in platforms.enabled_platforms(cfg):
        if kind not in p.kinds:
            log(f"{p.name}: skipped, it only takes videos.")
            continue
        if results:
            _pause_between_platforms()
        log(f"Uploading to {p.name}...")
        try:
            ref = p.post(out, p.make_caption(cfg, quote, author, caption), cfg)
        except Exception as e:  # noqa: BLE001 - any failure is reported, then the next platform goes
            log(f"{p.name}: ERROR {e}")
            results[p.key] = {"ok": False, "text": str(e)}
            platform_status.record(p.key, False, str(e))
        else:
            log(f"{p.name}: posted" + (f" {p.link(ref)}" if ref else ""))
            results[p.key] = {"ok": True, "text": str(ref or "")}
            platform_status.record(p.key, True, p.link(ref) if ref else "posted")
    return results


def run_once(post: bool) -> dict | None:
    """Render one piece of content. post=True also publishes (unless dry_run is on)."""
    if not _run_lock.acquire(blocking=False):
        log("Another run is already in progress, skipping.")
        return None
    STATE["busy"] = True
    wrote_run_file = False
    try:
        config.ensure_dirs()
        # Announce the run first, then look for an update. update.sh does the reverse (marker first, then
        # looks for the run file), so one of the two always notices the other.
        _write_run_file()
        wrote_run_file = True
        if update_in_progress():
            log("An update is being installed, so this run was skipped.")
            STATE["last_result"] = {"error": "An update is being installed. Try again in a minute."}
            return None
        cfg = config.load()
        mode = cfg["content_mode"]
        kind = random.choice(["reel", "reel", "photo"]) if mode == "mix" else mode
        quote, author = quotes.get_quote(cfg["quote_source"])
        log(f"Quote: “{quote}” — {author}")
        bg, source = media.pick_background(cfg)
        log(f"Background: {source}")
        if kind == "photo":
            out = reel.render_photo(bg, quote, author, cfg["watermark"])
        else:
            out = reel.render_reel(bg, quote, author, cfg["watermark"], int(cfg["reel_seconds"]))
        log(f"Rendered {kind}: {out.name}")
        caption = cfg["caption_template"].format(quote=quote, author=author, hashtags=cfg["hashtags"])
        result = {"time": time.strftime("%Y-%m-%d %H:%M:%S"), "kind": kind, "file": out.name,
                  "quote": quote, "author": author, "source": source, "posted": False, "code": None}
        if post and not cfg["dry_run"]:
            outcome = post_everywhere(cfg, kind, out, quote, author, caption)
            failed = {k: v["text"] for k, v in outcome.items() if not v["ok"]}
            if outcome and len(failed) == len(outcome):  # nothing got posted: same as a failed run
                names = {p.key: p.name for p in platforms.all_platforms()}
                raise RuntimeError(next(iter(failed.values())) if len(failed) == 1
                                   else "; ".join(f"{names[k]}: {v}" for k, v in failed.items()))
            if outcome:
                result.update(posted=True, code=(outcome.get("instagram") or {}).get("text") if (outcome.get("instagram") or {}).get("ok") else None)
            if len(outcome) > 1 or "instagram" not in outcome:
                result["platforms"] = {k: ("posted" if v["ok"] else "error: " + v["text"]) for k, v in outcome.items()}
        elif post:
            for p in platforms.enabled_platforms(cfg):
                if kind in p.kinds:
                    log(f"Dry run is ON - not posting to {p.name}.")
        _add_history(result)
        STATE["last_result"] = result
        return result
    except Exception as e:
        log(f"ERROR: {e}")
        STATE["last_result"] = {"error": str(e)}
        return None
    finally:
        if wrote_run_file:
            _remove_run_file()
        STATE["busy"] = False
        _run_lock.release()


def _latest_reel():
    reels = sorted(config.OUT.glob("*-reel.mp4"), key=lambda p: p.stat().st_mtime, reverse=True)
    return reels[0] if reels else None


def start_test_upload(key: str) -> tuple[bool, str]:
    """Upload the newest reel to one platform as private, in the background. Returns (started, message)."""
    p = platforms.get(key)
    if p is None or not p.can_test:
        return False, "That platform has no test upload."
    if STATE["busy"] or _run_lock.locked():
        return False, "A reel is being made right now. Try again in a minute."
    if update_in_progress():
        return False, "An update is being installed. Try again in a minute."
    if _latest_reel() is None:
        return False, "There's no reel to upload yet. Press Preview on the Home tab first."
    STATE["busy"] = True
    threading.Thread(target=_test_upload, args=(p,), daemon=True).start()
    return True, f"Uploading the latest reel to {p.name} as private. This can take a few minutes."


def _test_upload(p) -> None:
    if not _run_lock.acquire(blocking=False):
        STATE["busy"] = False
        return
    wrote = False
    try:
        config.ensure_dirs()
        _write_run_file()
        wrote = True
        reel_file = _latest_reel()
        cfg = config.load()
        log(f"{p.name}: test upload of {reel_file.name} (private)...")
        caption = p.make_caption(cfg, "Test upload", "LarpCron", "LarpCron test upload. Safe to delete.")
        try:
            ref = p.post(reel_file, caption, cfg, private=True)
        except Exception as e:  # noqa: BLE001
            log(f"{p.name}: ERROR {e}")
            platform_status.record(p.key, False, f"Test upload failed: {e}")
        else:
            log(f"{p.name}: test uploaded privately" + (f" {p.link(ref)}" if ref else ""))
            platform_status.record(p.key, True, "Test upload: " + (p.link(ref) if ref else "done"))
    finally:
        if wrote:
            _remove_run_file()
        STATE["busy"] = False
        _run_lock.release()


def run_async(post: bool) -> None:
    STATE["busy"] = True  # visible immediately, before the worker thread gets the lock
    threading.Thread(target=run_once, args=(post,), daemon=True).start()
