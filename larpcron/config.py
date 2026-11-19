"""Settings stored in data/config.json (gitignored). Edited from the web UI."""
import copy
import json
import threading
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data"
CACHE = DATA / "cache"
OUT = DATA / "out"
ASSETS = ROOT / "assets"
CONFIG_FILE = DATA / "config.json"

DEFAULTS = {
    "instagram_username": "",
    "instagram_password": "",
    "instagram_2fa_seed": "",      # optional TOTP secret, for accounts using an authenticator app
    "footage_source": "any",       # any (everything configured) | online (Pexels + Pixabay) | pexels | pixabay | tiktok | local
    "tiktok_profile": "https://www.tiktok.com/@waywroxc",  # used when footage_source is tiktok/any
    "pexels_api_key": "",          # free key: https://www.pexels.com/api/
    "pixabay_api_key": "",         # free key: https://pixabay.com/api/docs/
    "smart_music_start": True,     # start music at its best part (manual start times always win)
    "music_links": [],             # YouTube links, one per line: "<url> [start e.g. 1:12]"
    "auto_update": True,           # pull updates from the project's GitHub repo (see deploy/update.sh)
    "auto_update_deps": True,      # keep instagrapi and yt-dlp current (they break when the platforms change)
    "instagram_sessionid": "",     # sessionid cookie from a logged-in browser; skips password login when set
    "instagram_app_version": {},   # advanced override: {"app_version", "version_code", "bloks_versioning_id"}
    "ui_password": "larp",         # password for this web UI (username: admin). CHANGE IT.
    "enabled": False,              # scheduler on/off
    "dry_run": True,               # True = render everything but never touch Instagram
    "post_times": ["12:00", "19:00"],
    "timezone": "America/New_York",
    "schedule_mode": "weekdays",   # weekdays (pick days) | interval (every N days)
    "post_days": [0, 1, 2, 3, 4, 5, 6],  # Monday=0 ... Sunday=6, used by weekdays mode
    "every_n_days": 2,             # used by interval mode
    "interval_anchor": "",         # YYYY-MM-DD, the day an every-N-days cycle starts from
    "time_jitter_min": 0,          # shift each post by a random +/- this many minutes
    "post_chance": 100,            # percent chance each scheduled slot actually posts
    "pause_until": "",             # YYYY-MM-DD, no posts through this date
    "content_mode": "reel",        # reel | photo | mix
    "reel_seconds": 9,
    "themes": [
        "luxury car", "supercar night", "miami skyline", "miami beach aerial",
        "luxury watch", "yacht", "private jet", "city skyline night",
        "lamborghini", "rolls royce", "penthouse view", "dubai skyline",
    ],
    "quote_source": "mixed",       # local | online | mixed
    "hashtags": "#luxury #millionairemindset #miami #supercars #watches #grindset #mindset #success",
    "caption_template": "{quote}\n\n- {author}\n\n{hashtags}",
    "watermark": "",               # optional small text at the bottom of the video
}

_lock = threading.Lock()


def load() -> dict:
    cfg = copy.deepcopy(DEFAULTS)
    if CONFIG_FILE.exists():
        try:
            cfg.update(json.loads(CONFIG_FILE.read_text(encoding="utf-8")))
        except (OSError, ValueError):
            pass
    if not isinstance(cfg.get("instagram_app_version"), dict):  # an older config stored a plain string
        old = cfg.get("instagram_app_version")
        cfg["instagram_app_version"] = {"app_version": old} if old else {}
    return cfg


def save(cfg: dict) -> None:
    DATA.mkdir(parents=True, exist_ok=True)
    with _lock:
        tmp = CONFIG_FILE.with_suffix(".tmp")
        tmp.write_text(json.dumps(cfg, indent=2), encoding="utf-8")
        tmp.replace(CONFIG_FILE)


def ensure_dirs() -> None:
    for d in (DATA, CACHE, OUT):
        d.mkdir(parents=True, exist_ok=True)
