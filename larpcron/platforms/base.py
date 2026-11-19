"""What every posting platform looks like. job.py only talks to this interface."""
import random
import re
import subprocess
import time
from pathlib import Path


class PlatformError(RuntimeError):
    """A failure with a message that is fine to show in the panel."""


class Permanent(PlatformError):
    """A failure that trying again cannot fix, such as a missing browser."""


class CookieExpired(Permanent):
    """The saved browser cookies no longer log in."""


class Platform:
    key = ""             # short id used in config keys, status files and form fields
    name = ""            # what the person sees in logs and the panel
    kinds = ("reel",)    # what it can post: "reel" (video) and/or "photo"
    browser = False      # needs Playwright's Chromium (see deploy/install-browser.sh)
    can_test = False     # has a private "Test upload"
    cookie_domains = ()  # cookies for these sites are kept from a pasted export
    cookie_names = ()    # at least one of these must be in it, or it isn't a logged-in session
    test_warning = ""    # shown before a Test upload when the platform can't promise it stays private

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
    """Call fn(). If it fails, wait a little and try once more. A Permanent error (expired cookies, no browser) is never retried."""
    for attempt in range(1, attempts + 1):
        try:
            return fn()
        except Permanent:
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


def browser_installed() -> bool:
    """True when Playwright's Chromium has been downloaded (deploy/install-browser.sh). Doesn't start anything."""
    import os
    import sys

    root = os.environ.get("PLAYWRIGHT_BROWSERS_PATH")
    if root == "0":
        return False  # browsers live inside the package, which we don't go digging in
    if root:
        base = Path(root)
    elif sys.platform == "win32":
        base = Path(os.environ.get("LOCALAPPDATA", "")) / "ms-playwright"
    elif sys.platform == "darwin":
        base = Path.home() / "Library" / "Caches" / "ms-playwright"
    else:
        base = Path.home() / ".cache" / "ms-playwright"
    return any(base.glob("chromium-*")) if base.is_dir() else False


DEBUG_DIR = None  # set lazily, so tests can point it somewhere else


def save_debug_screenshot(page, key: str) -> str:
    """Save what the browser was showing when an upload failed. Returns the file name, or "" if it couldn't."""
    from .. import config

    folder = DEBUG_DIR or (config.DATA / "debug")
    try:
        folder.mkdir(parents=True, exist_ok=True)
        dest = folder / f"{key}-{time.strftime('%Y%m%d-%H%M%S')}.png"
        page.screenshot(path=str(dest), full_page=True, timeout=15000)
        for old in sorted(folder.glob(f"{key}-*.png"))[:-10]:  # keep the newest ten per platform
            old.unlink(missing_ok=True)
        return dest.name
    except Exception:  # noqa: BLE001 - a missing screenshot must never hide the real error
        return ""
