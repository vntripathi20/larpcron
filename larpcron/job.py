"""The pipeline: pick footage + quote -> render -> (optionally) post. Keeps a rolling log and history."""
import json
import random
import threading
import time
from collections import deque

from . import config, instagram, media, quotes, reel

_run_lock = threading.Lock()
LOG: deque = deque(maxlen=200)
STATE = {"busy": False, "last_result": None}
HISTORY_FILE = config.DATA / "history.json"


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


def run_once(post: bool) -> dict | None:
    """Render one piece of content. post=True also publishes (unless dry_run is on)."""
    if not _run_lock.acquire(blocking=False):
        log("Another run is already in progress, skipping.")
        return None
    STATE["busy"] = True
    try:
        config.ensure_dirs()
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
            log("Uploading to Instagram...")
            code = (instagram.post_reel if kind == "reel" else instagram.post_photo)(cfg, out, caption)
            result.update(posted=True, code=code)
            log(f"Posted! https://www.instagram.com/p/{code}/")
        elif post:
            log("Dry run is ON - not posting to Instagram.")
        _add_history(result)
        STATE["last_result"] = result
        return result
    except Exception as e:
        log(f"ERROR: {e}")
        STATE["last_result"] = {"error": str(e)}
        return None
    finally:
        STATE["busy"] = False
        _run_lock.release()


def run_async(post: bool) -> None:
    STATE["busy"] = True  # visible immediately, before the worker thread gets the lock
    threading.Thread(target=run_once, args=(post,), daemon=True).start()
