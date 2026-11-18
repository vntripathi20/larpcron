"""Split a long compilation video into separate clips (one per scene) in assets/backgrounds/.

    python -m larpcron split my_compilation.mp4 [--threshold 0.35] [--min 3] [--max 15]
"""
import re
import subprocess
from pathlib import Path

from .config import ASSETS
from .reel import ffmpeg_exe


def scene_times(src: Path, threshold: float) -> list[float]:
    p = subprocess.run(
        [ffmpeg_exe(), "-i", str(src), "-an", "-vf", f"select='gt(scene,{threshold})',showinfo", "-f", "null", "-"],
        capture_output=True, text=True,
    )
    return [float(m) for m in re.findall(r"pts_time:([\d.]+)", p.stderr)]


def duration(src: Path) -> float:
    p = subprocess.run([ffmpeg_exe(), "-i", str(src)], capture_output=True, text=True)
    m = re.search(r"Duration: (\d+):(\d+):([\d.]+)", p.stderr)
    if not m:
        raise RuntimeError("Could not read video duration")
    return int(m[1]) * 3600 + int(m[2]) * 60 + float(m[3])


def split(src: Path, threshold: float = 0.35, min_len: float = 3.0, max_len: float = 15.0) -> int:
    out_dir = ASSETS / "backgrounds"
    out_dir.mkdir(parents=True, exist_ok=True)
    cuts = [0.0] + scene_times(src, threshold) + [duration(src)]
    count = 0
    for a, b in zip(cuts, cuts[1:]):
        if b - a < min_len:
            continue
        length = min(b - a, max_len)
        dest = out_dir / f"{src.stem}_{count:03d}.mp4"
        subprocess.run(
            [ffmpeg_exe(), "-y", "-ss", f"{a:.2f}", "-i", str(src), "-t", f"{length:.2f}", "-an",
             "-c:v", "libx264", "-preset", "veryfast", "-crf", "20", "-pix_fmt", "yuv420p", str(dest)],
            capture_output=True,
        )
        count += 1
    return count
