"""What every posting platform looks like. job.py only talks to this interface."""
import random
import re
import subprocess
import time
from pathlib import Path


class PlatformError(RuntimeError):
    """A failure with a message that is fine to show in the panel."""


class CookieExpired(PlatformError):
    """The saved browser cookies no longer log in. Retrying will not help."""


class Platform:
    key = ""             # short id used in config keys, status files and form fields
    name = ""            # what the person sees in logs and the panel
    kinds = ("reel",)    # what it can post: "reel" (video) and/or "photo"
    browser = False      # needs Playwright's Chromium (see deploy/install-browser.sh)
    can_test = False     # has a private "Test upload"

    def enabled(self, cfg: dict) -> bool:
        raise NotImplementedError

    def make_caption(self, cfg: dict, quote: str, author: str, default: str) -> str:
        """The caption for this platform. The default is the main caption template, already filled in."""
        return default

    def link(self, ref: str) -> str:
        """A readable form of what post() returned, for the log and the panel."""
        return ref

    def post(self, video_path: Path, caption: str, cfg: dict, private: bool = False) -> str:
        """Upload one file. Returns a short result (a code or URL). Raises PlatformError with a clear message.

        private=True is for the Test upload button: use the most private setting the platform has.
        """
        raise NotImplementedError


def with_retry(fn, attempts: int = 2, pause=(20, 45), log=None):
    """Call fn(). If it fails, wait a little and try once more. A CookieExpired is never retried."""
    for attempt in range(1, attempts + 1):
        try:
            return fn()
        except CookieExpired:
            raise
        except Exception as e:
            if attempt == attempts:
                raise
            if log:
                log(f"First attempt failed ({str(e)[:160]}). Trying once more.")
            time.sleep(random.uniform(*pause))


def probe_video(path: Path) -> tuple[int, int, float]:
    """(width, height, seconds) read from ffmpeg's banner. Raises PlatformError if it can't tell."""
    from ..reel import ffmpeg_exe

    p = subprocess.run([ffmpeg_exe(), "-hide_banner", "-i", str(path)], capture_output=True, text=True)
    text = p.stderr
    dur = re.search(r"Duration:\s*(\d+):(\d+):(\d+(?:\.\d+)?)", text)
    size = re.search(r"Video:.*?(\d{2,5})x(\d{2,5})", text)
    if not dur or not size:
        raise PlatformError(f"Couldn't read the video {path.name}.")
    seconds = int(dur.group(1)) * 3600 + int(dur.group(2)) * 60 + float(dur.group(3))
    return int(size.group(1)), int(size.group(2)), seconds
