"""Background footage: Pexels video API (free key, free license) or your own clips in assets/backgrounds."""
import json
import random
import subprocess
import time
from pathlib import Path

import requests

from .config import ASSETS, CACHE, DATA

USED_FILE = DATA / "used_media.json"
VIDEO_EXT = {".mp4", ".mov", ".m4v", ".webm", ".mkv"}


def _used() -> list:
    try:
        return json.loads(USED_FILE.read_text())
    except (OSError, ValueError):
        return []


def _mark_used(key: str) -> None:
    used = [u for u in _used() if u != key][-200:] + [key]
    USED_FILE.write_text(json.dumps(used))


def _local_clips() -> list[Path]:
    d = ASSETS / "backgrounds"
    return [p for p in d.glob("*") if p.suffix.lower() in VIDEO_EXT] if d.exists() else []


def _trim_cache(keep: int = 15) -> None:
    files = sorted(CACHE.glob("*.mp4"), key=lambda p: p.stat().st_mtime, reverse=True)
    for old in files[keep:]:
        old.unlink(missing_ok=True)


def _pexels(cfg: dict, theme: str) -> Path:
    key = cfg.get("pexels_api_key", "").strip()
    r = requests.get(
        "https://api.pexels.com/videos/search",
        headers={"Authorization": key},
        params={"query": theme, "orientation": "portrait", "size": "medium", "per_page": 40},
        timeout=20,
    )
    r.raise_for_status()
    videos = r.json().get("videos", [])
    used = set(_used())
    fresh = [v for v in videos if f"pexels:{v['id']}" not in used] or videos
    if not fresh:
        raise RuntimeError(f"Pexels returned no videos for '{theme}'")
    video = random.choice(fresh)
    files = [f for f in video["video_files"] if f.get("file_type") == "video/mp4" and f.get("height")]
    # Prefer portrait files around 1080x1920; otherwise the tallest available.
    good = [f for f in files if f["height"] >= f["width"] and 1280 <= f["height"] <= 2200]
    chosen = (good or sorted(files, key=lambda f: f["height"], reverse=True))[0]
    CACHE.mkdir(parents=True, exist_ok=True)
    dest = CACHE / f"pexels_{video['id']}.mp4"
    if not dest.exists():
        with requests.get(chosen["link"], stream=True, timeout=60) as resp:
            resp.raise_for_status()
            tmp = dest.with_suffix(".part")
            with open(tmp, "wb") as fh:
                for chunk in resp.iter_content(1 << 20):
                    fh.write(chunk)
            tmp.replace(dest)
    _mark_used(f"pexels:{video['id']}")
    _trim_cache()
    return dest


def _download(url: str, dest: Path) -> Path:
    if not dest.exists():
        with requests.get(url, stream=True, timeout=60) as resp:
            resp.raise_for_status()
            tmp = dest.with_suffix(".part")
            with open(tmp, "wb") as fh:
                for chunk in resp.iter_content(1 << 20):
                    fh.write(chunk)
            tmp.replace(dest)
    return dest


def _pixabay(cfg: dict, theme: str) -> Path:
    r = requests.get(
        "https://pixabay.com/api/videos/",
        params={"key": cfg["pixabay_api_key"].strip(), "q": theme, "per_page": 50, "safesearch": "true"},
        timeout=20,
    )
    r.raise_for_status()
    hits = r.json().get("hits", [])
    used = set(_used())
    fresh = [h for h in hits if f"pixabay:{h['id']}" not in used] or hits
    if not fresh:
        raise RuntimeError(f"Pixabay returned no videos for '{theme}'")
    hit = random.choice(fresh)
    sizes = [hit["videos"][k] for k in ("large", "medium", "small") if hit["videos"].get(k, {}).get("url")]
    # Prefer a rendition at least ~1080 on its short side; the renderer center-crops to 9:16.
    big = [v for v in sizes if min(v["width"], v["height"]) >= 1000]
    chosen = big[-1] if big else sizes[0]  # smallest adequate rendition, else the largest available
    CACHE.mkdir(parents=True, exist_ok=True)
    dest = _download(chosen["url"], CACHE / f"pixabay_{hit['id']}.mp4")
    _mark_used(f"pixabay:{hit['id']}")
    _trim_cache()
    return dest


def _tiktok(cfg: dict, theme: str) -> Path:
    """Random clip from a TikTok creator's profile (yt-dlp), audio stripped. theme is unused."""
    import yt_dlp

    from .reel import ffmpeg_exe

    profile = cfg.get("tiktok_profile", "").strip()
    if not profile:
        raise RuntimeError("No TikTok profile URL set")
    index = DATA / "tiktok_index.json"
    ids = []
    try:  # re-list the profile at most once a day
        cached = json.loads(index.read_text())
        if cached["profile"] == profile and time.time() - cached["at"] < 86400 and cached["ids"]:
            ids = cached["ids"]
    except (OSError, ValueError, KeyError):
        pass
    quiet = {"quiet": True, "no_warnings": True}
    if not ids:
        with yt_dlp.YoutubeDL({**quiet, "extract_flat": True, "playlistend": 200}) as ydl:
            info = ydl.extract_info(profile, download=False)
        ids = [e["id"] for e in info.get("entries", []) if e and e.get("id")]
        if not ids:
            raise RuntimeError("TikTok profile listing came back empty")
        DATA.mkdir(parents=True, exist_ok=True)
        index.write_text(json.dumps({"profile": profile, "at": time.time(), "ids": ids}))
    used = set(_used())
    vid = random.choice([i for i in ids if f"tiktok:{i}" not in used] or ids)
    CACHE.mkdir(parents=True, exist_ok=True)
    dest = CACHE / f"tiktok_{vid}.mp4"
    if not dest.exists():
        raw = CACHE / f"tiktok_{vid}.raw.mp4"
        url = f"{profile.rstrip('/')}/video/{vid}"
        opts = {**quiet, "format": "best[ext=mp4]/best", "outtmpl": str(raw)}
        with yt_dlp.YoutubeDL(opts) as ydl:
            ydl.download([url])
        # Remove the audio track (-an) without re-encoding the video.
        r = subprocess.run([ffmpeg_exe(), "-y", "-i", str(raw), "-an", "-c:v", "copy", str(dest)],
                           capture_output=True, text=True)
        raw.unlink(missing_ok=True)
        if r.returncode != 0:
            raise RuntimeError("ffmpeg audio strip failed: " + r.stderr[-300:])
    _mark_used(f"tiktok:{vid}")
    _trim_cache()
    return dest


def pick_background(cfg: dict) -> tuple[Path, str]:
    """Returns (video path, human-readable source).

    footage_source: online (Pexels + Pixabay) | pexels | pixabay | tiktok | local | any (online + local)
    Sources are tried in random order; ones without a key or that fail are skipped.
    """
    source = cfg.get("footage_source", "any")
    themes = cfg["themes"] or ["luxury car"]
    attempts = []
    if source in ("online", "pexels", "any") and cfg.get("pexels_api_key", "").strip():
        attempts.append(("Pexels", _pexels))
    if source in ("online", "pixabay", "any") and cfg.get("pixabay_api_key", "").strip():
        attempts.append(("Pixabay", _pixabay))
    if source in ("tiktok", "any") and cfg.get("tiktok_profile", "").strip():
        attempts.append(("TikTok", _tiktok))
    random.shuffle(attempts)
    errors = []
    for name, fn in attempts:
        theme = random.choice(themes)
        try:
            return fn(cfg, theme), f"{name}: {theme}" if name != "TikTok" else "TikTok clip"
        except Exception as e:
            errors.append(f"{name}: {e}")
    local = _local_clips() if source in ("local", "any") or errors else []
    if local:
        clip = random.choice(local)
        return clip, f"local: {clip.name}"
    if errors:
        raise RuntimeError("Online footage failed - " + "; ".join(errors))
    raise RuntimeError(
        "No footage available for source '" + source + "'. Set Footage source to 'tiktok' or 'any', "
        "add a free Pexels/Pixabay API key in the panel, or put clips in assets/backgrounds/."
    )
