"""TikTok through a logged-in browser session, using the tiktok-uploader library (Playwright underneath).

There is no official posting API here. The cookies come from a browser you are logged in with.
Automated posting is against TikTok's rules and can get the account flagged. Use one you can lose.
"""
import contextlib
import io
import logging
import re
from pathlib import Path

from .. import config
from . import cookies as cookie_store
from .base import CookieExpired, Permanent, Platform, PlatformError, probe_video, save_debug_screenshot, with_retry

DOMAINS = ("tiktok.com",)
LOGIN_COOKIES = ("sessionid", "sessionid_ss")
MAX_DESCRIPTION = 150          # tiktok-uploader refuses longer descriptions
EXPIRED = "TikTok cookies expired. Re-export from a logged-in browser."
NO_BROWSER = "Chromium isn't installed for Playwright. Run deploy/install-browser.sh on this machine."


def fit_description(text: str, limit: int = MAX_DESCRIPTION) -> str:
    """Cut to the limit at a word boundary, so a hashtag is never chopped in half."""
    text = " ".join(text.split())
    if len(text) <= limit:
        return text
    cut = text[:limit]
    return (cut.rsplit(" ", 1)[0] if " " in cut else cut).rstrip()


@contextlib.contextmanager
def _quiet_library():
    """tiktok-uploader prints every cookie it adds. Swallow its output so cookies never reach the journal,
    and collect its error messages, which is how it reports why an upload failed."""
    messages: list[str] = []

    class Collect(logging.Handler):
        def emit(self, record):
            if record.levelno >= logging.ERROR:
                messages.append(record.getMessage())

    log = logging.getLogger("tiktok_uploader")
    handler = Collect()
    log.addHandler(handler)
    try:
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            yield messages
    finally:
        log.removeHandler(handler)


@contextlib.contextmanager
def _track_playwright():
    """tiktok-uploader starts a Playwright driver process and never stops it. Remember it so we can."""
    from playwright.sync_api import sync_playwright
    import tiktok_uploader.browsers as browsers

    started = []

    def tracked():
        manager = sync_playwright()
        real_start = manager.start

        def start():
            pw = real_start()
            started.append(pw)
            return pw

        manager.start = start
        return manager

    original = browsers.sync_playwright
    browsers.sync_playwright = tracked
    try:
        yield started
    finally:
        browsers.sync_playwright = original
        for pw in started:
            try:
                pw.stop()
            except Exception:  # noqa: BLE001
                pass


def _classify(exc: Exception) -> PlatformError:
    text = f"{type(exc).__name__}: {exc}"
    if re.search(r"InsufficientAuth|Authentication failed|sessionid", text):
        return CookieExpired(EXPIRED)
    if re.search(r"Executable doesn't exist|playwright install", text):
        return Permanent(NO_BROWSER)
    return PlatformError("TikTok login check failed: " + str(exc).strip().splitlines()[0][:200])


class TikTok(Platform):
    key = "tiktok"
    name = "TikTok"
    kinds = ("reel",)
    browser = True
    can_test = True

    def enabled(self, cfg: dict) -> bool:
        return bool(cfg.get("tiktok_enabled"))

    def make_caption(self, cfg: dict, quote: str, author: str, default: str) -> str:
        template = (cfg.get("tiktok_caption_template") or "").strip()
        text = default
        if template:
            try:
                text = template.format(quote=quote, author=author, hashtags=cfg.get("hashtags", ""))
            except (KeyError, IndexError, ValueError):
                pass
        return fit_description(text)

    def post(self, video_path: Path, caption: str, cfg: dict, private: bool = False) -> str:
        from .. import job

        if not cookie_store.exists(self.key):
            raise CookieExpired("TikTok has no cookies saved. Paste an export in the Platforms tab.")
        wide, high, seconds = probe_video(video_path)
        if high <= wide:
            raise PlatformError("TikTok needs a vertical video.")
        return with_retry(lambda: self._upload_once(video_path, caption, private), log=job.log)

    def _upload_once(self, video_path: Path, caption: str, private: bool) -> str:
        from tiktok_uploader.upload import TikTokUploader

        uploader = None
        with _quiet_library() as messages, _track_playwright():
            try:
                uploader = TikTokUploader(cookies=str(cookie_store.path_for(self.key)), headless=True, browser="chromium")
                try:
                    uploader.page  # starts the browser and logs in with the cookies
                except Exception as e:  # noqa: BLE001
                    raise _classify(e) from None
                video = {"path": str(video_path), "description": caption,
                         "visibility": "only_you" if private else "everyone"}
                failed = uploader.upload_videos([video])
                if failed:
                    why = messages[-1] if messages else "no reason given"
                    if re.search(r"InsufficientAuth|log ?in", why, re.IGNORECASE):
                        raise CookieExpired(EXPIRED)
                    raise PlatformError("TikTok didn't accept the upload: " + why[:200])
                return "uploaded privately" if private else "uploaded"
            except PlatformError as e:
                self._screenshot(uploader, e)
                raise
            except Exception as e:  # noqa: BLE001
                err = _classify(e) if not isinstance(e, PlatformError) else e
                self._screenshot(uploader, err)
                raise err from None
            finally:
                if uploader is not None:
                    uploader.close()

    def _screenshot(self, uploader, err: Exception) -> None:
        if isinstance(err, CookieExpired) or uploader is None or getattr(uploader, "_page", None) is None:
            return
        name = save_debug_screenshot(uploader._page, self.key)
        if name:
            err.args = (f"{err.args[0]} (screenshot: data/debug/{name})",) if err.args else (name,)
