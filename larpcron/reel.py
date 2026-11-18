"""Render a 1080x1920 reel (or a still photo post) from a background clip + quote."""
import json
import random
import shutil
import subprocess
import time
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

from .config import ASSETS, OUT

W, H = 1080, 1920
FONT_CANDIDATES = [
    "C:/Windows/Fonts/georgiab.ttf", "C:/Windows/Fonts/arialbd.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSerif-Bold.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
    "/usr/share/fonts/truetype/liberation/LiberationSerif-Bold.ttf",
    "/System/Library/Fonts/Supplemental/Georgia Bold.ttf",
]


def _font(size: int) -> ImageFont.FreeTypeFont:
    for path in FONT_CANDIDATES:
        if Path(path).exists():
            return ImageFont.truetype(path, size)
    return ImageFont.load_default(size)


def _wrap(draw, text, font, max_w):
    lines, line = [], ""
    for word in text.split():
        trial = f"{line} {word}".strip()
        if draw.textlength(trial, font=font) <= max_w or not line:
            line = trial
        else:
            lines.append(line)
            line = word
    if line:
        lines.append(line)
    return lines


def make_overlay(quote: str, author: str, watermark: str, dest: Path) -> Path:
    img = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    size = 84
    while True:  # shrink until the quote fits in ~45% of the frame height
        font = _font(size)
        lines = _wrap(d, f"\u201c{quote}\u201d", font, W - 180)
        line_h = int(size * 1.25)
        if len(lines) * line_h <= H * 0.45 or size <= 44:
            break
        size -= 4
    total = len(lines) * line_h
    y = (H - total) // 2 - 60
    for ln in lines:
        x = (W - d.textlength(ln, font=font)) / 2
        d.text((x + 3, y + 3), ln, font=font, fill=(0, 0, 0, 200))
        d.text((x, y), ln, font=font, fill=(255, 255, 255, 255))
        y += line_h
    af = _font(46)
    at = f"\u2014 {author}"
    ax = (W - d.textlength(at, font=af)) / 2
    d.text((ax + 2, y + 42), at, font=af, fill=(0, 0, 0, 220))
    d.text((ax, y + 40), at, font=af, fill=(240, 205, 120, 255))
    if watermark:
        wf = _font(36)
        d.text(((W - d.textlength(watermark, font=wf)) / 2, H - 260), watermark, font=wf, fill=(255, 255, 255, 170))
    img.save(dest)
    return dest


def ffmpeg_exe() -> str:
    found = shutil.which("ffmpeg")
    if found:
        return found
    import imageio_ffmpeg
    return imageio_ffmpeg.get_ffmpeg_exe()


def _run(cmd: list[str]) -> None:
    p = subprocess.run(cmd, capture_output=True, text=True)
    if p.returncode != 0:
        raise RuntimeError("ffmpeg failed:\n" + p.stderr[-1500:])


_VF = (
    f"[0:v]scale={W}:{H}:force_original_aspect_ratio=increase,crop={W}:{H},"
    "eq=brightness=-0.14:saturation=1.1,setsar=1[bg];"
    "[bg][1:v]overlay=0:0:format=auto"
)


AUDIO_EXT = {".mp3", ".m4a", ".wav", ".aac", ".ogg", ".flac"}


def parse_ts(value) -> float:
    """'72', '1:12' or '1:02:03' -> seconds."""
    secs = 0.0
    for part in str(value).strip().split(":"):
        secs = secs * 60 + float(part)
    return secs


def pick_music(seconds: int) -> tuple[list[str], list[str]]:
    """Returns (ffmpeg input args for the audio, output audio-filter args).

    assets/music/playlist.json (optional): [{"file": "a.mp3", "start": "1:12", "volume": 0.8}, ...]
    Without it, any audio file in assets/music/ is used from 0:00. With no music at all: silence.
    """
    mdir = ASSETS / "music"
    entries = []
    pl = mdir / "playlist.json"
    if pl.exists():
        try:
            for e in json.loads(pl.read_text(encoding="utf-8")):
                if (mdir / e["file"]).is_file():
                    entries.append(e)
        except (OSError, ValueError, KeyError, TypeError):
            pass
    if not entries:
        entries = [{"file": p.name} for p in mdir.glob("*") if p.suffix.lower() in AUDIO_EXT]
    from . import config as _config
    from . import music as _music

    for line in _config.load().get("music_links", []):
        parsed = _music.parse_line(line)
        if parsed:
            entries.append(parsed)
    e = random.choice(entries) if entries else None
    if e and "link" in e:
        try:
            e = {**e, "file": str(_music.fetch(e["link"]))}
        except Exception as ex:  # fall back to a local track, else silence
            print(f"Music link failed ({e['link']}): {ex}", flush=True)
            local = [x for x in entries if "file" in x]
            e = random.choice(local) if local else None
    if not e:
        return ["-f", "lavfi", "-i", "anullsrc=channel_layout=stereo:sample_rate=44100"], []
    args = []
    start = None
    if e.get("start") not in (None, ""):  # a manual start time always wins
        start = parse_ts(e["start"])
    elif _config.load().get("smart_music_start"):
        start, how = _music.smart_start(e.get("link"), mdir / e["file"], seconds)
        if start is not None:
            print(f"Smart music start: {start:.1f}s ({how})", flush=True)
    if start:
        args += ["-ss", f"{start:.2f}"]
    args += ["-stream_loop", "-1", "-i", str(mdir / e["file"])]
    vol = float(e.get("volume", 1.0))
    af = f"volume={vol},afade=t=in:st=0:d=0.4,afade=t=out:st={max(seconds - 1, 0)}:d=1"
    return args, ["-af", af]


def render_reel(bg: Path, quote: str, author: str, watermark: str, seconds: int) -> Path:
    OUT.mkdir(parents=True, exist_ok=True)
    stamp = time.strftime("%Y%m%d-%H%M%S")
    overlay = make_overlay(quote, author, watermark, OUT / f"{stamp}-overlay.png")
    cmd = [ffmpeg_exe(), "-y", "-stream_loop", "-1", "-i", str(bg), "-loop", "1", "-i", str(overlay)]
    audio_in, audio_filter = pick_music(seconds)
    cmd += audio_in
    dest = OUT / f"{stamp}-reel.mp4"
    tmp = dest.with_name(dest.name + ".part")  # written here, renamed when complete (never a half-written *.mp4)
    cmd += [
        "-filter_complex", _VF + f",fade=t=in:st=0:d=0.6,fade=t=out:st={seconds - 0.6}:d=0.6,format=yuv420p[v]",
        "-map", "[v]", "-map", "2:a", *audio_filter, "-t", str(seconds), "-r", "30",
        "-c:v", "libx264", "-preset", "veryfast", "-crf", "23",
        "-c:a", "aac", "-b:a", "128k", "-movflags", "+faststart", "-f", "mp4", str(tmp),
    ]
    try:
        _run(cmd)
        tmp.replace(dest)
    finally:
        overlay.unlink(missing_ok=True)
        tmp.unlink(missing_ok=True)
    try:  # poster frame for the panel's player (mid-reel, so the quote is visible)
        subprocess.run([ffmpeg_exe(), "-y", "-ss", str(seconds / 2), "-i", str(dest), "-frames:v", "1",
                        "-vf", "scale=540:-1", "-q:v", "4", str(dest.with_suffix(".jpg"))],
                       capture_output=True, timeout=60)
    except Exception:
        pass
    return dest


def render_photo(bg: Path, quote: str, author: str, watermark: str) -> Path:
    OUT.mkdir(parents=True, exist_ok=True)
    stamp = time.strftime("%Y%m%d-%H%M%S")
    overlay = make_overlay(quote, author, watermark, OUT / f"{stamp}-overlay.png")
    dest = OUT / f"{stamp}-post.jpg"
    tmp = dest.with_name(dest.name + ".part")  # renamed when complete, like reels
    # Feed posts are 4:5 -> crop the 9:16 composite to 1080x1350.
    cmd = [
        ffmpeg_exe(), "-y", "-ss", "1", "-i", str(bg), "-i", str(overlay),
        "-filter_complex", _VF + f",crop={W}:1350,format=yuvj420p[v]",
        "-map", "[v]", "-frames:v", "1", "-q:v", "2", "-f", "image2", str(tmp),
    ]
    try:
        _run(cmd)
        tmp.replace(dest)
    finally:
        overlay.unlink(missing_ok=True)
        tmp.unlink(missing_ok=True)
    return dest
