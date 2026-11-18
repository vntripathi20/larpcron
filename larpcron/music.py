"""Music from YouTube links (optional). Audio is downloaded once with yt-dlp and cached.

You are responsible for making sure you have the right to use any track you add.
"""
import re
from pathlib import Path

from .config import DATA

MUSIC_CACHE = DATA / "music_cache"
_ID_RE = re.compile(r"(?:v=|youtu\.be/|shorts/|embed/)([\w-]{11})")
_URL_RE = re.compile(r"^\s*(https?://\S+)(?:[\s,]+([\d:.]+))?\s*$")


def is_spotify(line: str) -> bool:
    return "spotify.com" in line or line.strip().startswith("spotify:")


def video_id(url: str) -> str | None:
    m = _ID_RE.search(url)
    return m.group(1) if m else None


def parse_line(line: str) -> dict | None:
    """'https://youtu.be/XXXXXXXXXXX 1:12' -> {"link": url, "start": "1:12"}; None if unusable."""
    m = _URL_RE.match(line)
    if not m or not video_id(m.group(1)):
        return None
    entry = {"link": m.group(1)}
    if m.group(2):
        entry["start"] = m.group(2)
    return entry


def fetch(url: str) -> Path:
    """Download (or reuse cached) audio for a YouTube link; returns the local file."""
    import yt_dlp

    vid = video_id(url)
    if not vid:
        raise ValueError(f"Not a YouTube link: {url}")
    MUSIC_CACHE.mkdir(parents=True, exist_ok=True)
    for existing in MUSIC_CACHE.glob(f"yt_{vid}.*"):
        if existing.suffix != ".part":
            return existing
    opts = {
        "quiet": True, "no_warnings": True, "noplaylist": True,
        "format": "bestaudio[ext=m4a]/bestaudio/best",
        "outtmpl": str(MUSIC_CACHE / f"yt_{vid}.%(ext)s"),
    }
    with yt_dlp.YoutubeDL(opts) as ydl:
        ydl.download([url])
    for f in MUSIC_CACHE.glob(f"yt_{vid}.*"):
        if f.suffix != ".part":
            return f
    raise RuntimeError("Audio download produced no file")


# ---------------------------------------------------------------- smart start
import json  # noqa: E402
import subprocess  # noqa: E402

SMART_CACHE = MUSIC_CACHE / "smart_starts.json"


def best_heatmap_start(heatmap: list[dict], seconds: float, duration: float | None = None) -> float | None:
    """Start time of the window (as long as the reel) with the highest total 'Most replayed' value."""
    segs = [h for h in heatmap or [] if "start_time" in h and "end_time" in h and "value" in h]
    if not segs:
        return None
    seg_len = max(segs[0]["end_time"] - segs[0]["start_time"], 0.1)
    n = max(1, round(seconds / seg_len))
    best_i, best_sum = 0, -1.0
    for i in range(0, max(1, len(segs) - n + 1)):
        total = sum(s["value"] for s in segs[i:i + n])
        if total > best_sum:  # strict '>' keeps the earliest of equal windows
            best_i, best_sum = i, total
    start = float(segs[best_i]["start_time"])
    if duration:
        start = min(start, max(duration - seconds, 0.0))
    return max(start, 0.0)


def heatmap_start(url: str, seconds: float) -> float | None:
    """Best start from YouTube's 'Most replayed' graph, or None if the video has no heatmap."""
    import yt_dlp

    with yt_dlp.YoutubeDL({"quiet": True, "no_warnings": True, "noplaylist": True}) as ydl:
        info = ydl.extract_info(url, download=False)
    return best_heatmap_start(info.get("heatmap"), seconds, info.get("duration"))


def loudest_start(path: Path, seconds: float, frame: float = 0.25, max_len: int = 900) -> float:
    """Start of the loudest window of `seconds` in an audio file (RMS energy via ffmpeg + numpy)."""
    import numpy as np

    from .reel import ffmpeg_exe

    rate = 8000
    p = subprocess.run(
        [ffmpeg_exe(), "-v", "error", "-i", str(path), "-t", str(max_len), "-vn", "-ac", "1", "-ar", str(rate),
         "-f", "s16le", "-"],
        capture_output=True,
    )
    if p.returncode != 0 or not p.stdout:
        raise RuntimeError("could not decode audio for analysis")
    x = np.frombuffer(p.stdout, dtype=np.int16).astype(np.float32) / 32768.0
    step = int(rate * frame)
    n_frames = len(x) // step
    win = max(1, int(round(seconds / frame)))
    if n_frames <= win:
        return 0.0
    rms = np.sqrt((x[: n_frames * step].reshape(n_frames, step) ** 2).mean(axis=1))
    csum = np.concatenate([[0.0], np.cumsum(rms)])
    window_sums = csum[win:] - csum[:-win]  # one value per valid window start
    return float(np.argmax(window_sums) * frame)


def smart_start(link: str | None, path: Path, seconds: float) -> tuple[float | None, str]:
    """Best music start: YouTube heatmap when available, else loudest window. Cached per track + length."""
    key = f"{path.name}:{int(seconds)}"
    try:
        cache = json.loads(SMART_CACHE.read_text())
    except (OSError, ValueError):
        cache = {}
    if key in cache:
        return cache[key]["start"], cache[key]["how"] + " (cached)"
    start, how = None, ""
    if link:
        try:
            start = heatmap_start(link, seconds)
            how = "YouTube most-replayed"
        except Exception as e:
            print(f"Heatmap lookup failed: {e}", flush=True)
    if start is None:
        try:
            start, how = loudest_start(path, seconds), "loudest window"
        except Exception as e:
            print(f"Loudest-window analysis failed: {e}", flush=True)
            return None, ""
    MUSIC_CACHE.mkdir(parents=True, exist_ok=True)
    cache[key] = {"start": start, "how": how}
    SMART_CACHE.write_text(json.dumps(cache))
    return start, how
