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
from . import browser as browser_helpers
from . import cookies as cookie_store
from .base import CookieExpired, Permanent, Platform, PlatformError, probe_video, save_debug_screenshot, with_retry

# ---- what the login check looks for on tiktok.com (change here if TikTok changes its page) -----------------
SELECTORS = {
    "main_url": "https://www.tiktok.com/",
    "logged_in": "[data-e2e='profile-icon']",          # the avatar menu, only there when logged in
    "logged_out": "[data-e2e='top-login-button']",     # the Log in button, only there when logged out
}
LOGIN_URL_PARTS = ("/login",)
# -------------------------------------------------------------------------------------------------------

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
def _light_browser(cfg: dict):
    """Swap tiktok-uploader's browser launcher for one that suits a small machine, and its login check for one
    that waits for a specific element. Everything is put back afterwards.

    tiktok-uploader starts a Playwright driver process and never stops it, so ours is remembered and stopped.
    Yields (drivers started, action timeout in ms, the light page switches)."""
    from playwright.sync_api import sync_playwright
    import tiktok_uploader
    import tiktok_uploader.upload as upload_module

    nav_ms, action_ms = browser_helpers.timeouts(cfg)
    started: list = []
    lights: list = []

    def get_browser(name="chromium", headless=True, proxy=None, *args, **kwargs):
        pw = sync_playwright().start()
        started.append(pw)
        browser = pw.chromium.launch(headless=True, args=browser_helpers.launch_args())
        context = browser.new_context(viewport=browser_helpers.VIEWPORT, locale="en-US",
                                      user_agent=tiktok_uploader.config.disguising.user_agent)
        context.add_init_script("Object.defineProperty(navigator, 'webdriver', { get: () => undefined });")
        page = context.new_page()
        browser_helpers.tune_page(page, "tiktok", "TikTok", nav_ms, action_ms)
        lights.append(browser_helpers.LightPage(page).on())  # no images, fonts, media or analytics until the login is checked
        return page

    original = upload_module.get_browser
    upload_module.get_browser = get_browser
    try:
        with browser_helpers.library_waits(tiktok_uploader.config, action_ms // 1000):
            yield started, action_ms, lights
    finally:
        upload_module.get_browser = original
        for pw in started:
            try:
                pw.stop()
            except Exception:  # noqa: BLE001
                pass


def _check_login(page, action_ms: int) -> None:
    """Add the cookies, open tiktok.com and wait for the avatar (logged in) or the Log in button (logged out).
    Replaces tiktok-uploader's own check, which waits for the whole page to load. Never prints cookies."""
    S = SELECTORS
    jar = cookie_store.to_playwright(cookie_store.load("tiktok"))
    try:
        page.context.add_cookies(jar)
    except Exception:  # noqa: BLE001 - one odd cookie shouldn't sink the rest
        for one in jar:
            try:
                page.context.add_cookies([one])
            except Exception:  # noqa: BLE001
                pass
    page.goto(S["main_url"])  # already patched: domcontentloaded, longer timeout, one retry
    if any(part in page.url for part in LOGIN_URL_PARTS):
        raise CookieExpired(EXPIRED)
    try:
        page.wait_for_selector(f"{S['logged_in']}, {S['logged_out']}", timeout=action_ms)
    except Exception as e:  # noqa: BLE001
        if not browser_helpers.is_timeout(e):
            raise
        # The page may have changed so the indicators no longer match. The upload step is a login check too,
        # so carry on and let that decide, instead of failing on a stale selector.
        from .. import job

        job.log("TikTok: couldn't find the login indicator on the page, going on to the upload anyway.")
        return
    if page.query_selector(S["logged_out"]) and not page.query_selector(S["logged_in"]):
        raise CookieExpired(EXPIRED)


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
    cookie_domains = DOMAINS
    cookie_names = LOGIN_COOKIES
    test_warning = ("TikTok's page is automated and the Only you setting can't be double checked. "
                    "If TikTok has changed its page, the test could end up public.")

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
        return with_retry(lambda: self._upload_once(video_path, caption, private, cfg), log=job.log)

    def _upload_once(self, video_path: Path, caption: str, private: bool, cfg: dict) -> str:
        from tiktok_uploader.upload import TikTokUploader

        uploader = None
        with _quiet_library() as messages, _light_browser(cfg) as (_, action_ms, lights):
            try:
                uploader = TikTokUploader(cookies=str(cookie_store.path_for(self.key)), headless=True, browser="chromium")

                def authenticate(page):
                    try:
                        _check_login(page, action_ms)
                    finally:
                        for light in lights:  # the upload itself gets the full page
                            light.off()
                    return page

                uploader.auth.authenticate_agent = authenticate
                try:
                    uploader.page  # starts the browser and logs in with the cookies
                except PlatformError:
                    raise
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
        if err.args and "screenshot:" in str(err.args[0]):  # a navigation timeout already saved one
            return
        name = save_debug_screenshot(uploader._page, self.key)
        if name:
            err.args = (f"{err.args[0]} (screenshot: data/debug/{name})",) if err.args else (name,)
