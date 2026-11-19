"""TikTok through a logged-in browser session, using the tiktok-uploader library (Playwright underneath).

There is no official posting API here. The cookies come from a browser you are logged in with.
Automated posting is against TikTok's rules and can get the account flagged. Use one you can lose.
"""
import contextlib
import io
import logging
import re
import time
from pathlib import Path
from types import SimpleNamespace

from .. import config
from . import browser as browser_helpers
from . import cookies as cookie_store
from .base import CookieExpired, Permanent, Platform, PlatformError, probe_video, save_debug_screenshot, with_retry

# ---- what the login check looks for on tiktok.com (change here if TikTok changes its page) -----------------
SELECTORS = {
    "main_url": "https://www.tiktok.com/",
    "logged_in": "[data-e2e='profile-icon']",          # the avatar menu, only there when logged in
    "logged_out": "[data-e2e='top-login-button']",     # the Log in button, only there when logged out
    # Pop-ups that cover the upload page. Matched by what they say, not by CSS classes. Patterns are
    # case-insensitive regular expressions.
    "got_it": r"^\s*Got it\s*$",                           # the "New editing features added" tip card
    "tip_text": r"new editing features",                   # only used to name the tip in the log
    "checks_modal": r"automatic content checks",           # the "Turn on automatic content checks?" modal
    "modal_cancel": r"^\s*Cancel\s*$",                     # the ONLY button we press on that modal
    "close_names": r"^\s*(close|dismiss|not now|maybe later|skip|\u00d7|x)\s*$",  # for any other pop-up
    "dialog": "[role='dialog'], [aria-modal='true']",
    # The final step.
    "post_button": "[data-e2e='post_video_button']",
    "post_enabled_attr": "data-disabled",                  # "false" once the video has finished processing
    "post_now": r"^\s*Post now\s*$",                       # the "post anyway" button TikTok sometimes asks for
    "post_confirmation": r"Your video has been uploaded|Video published|\u89c6\u9891\u5df2\u53d1\u5e03",
}
# Never pressed, whatever pop-up shows it: "Turn on" and anything else that would change the account's settings.
NEVER_PRESS = re.compile(r"turn on|enable|allow|agree|accept|confirm|delete|remove|upgrade", re.IGNORECASE)
OVERLAY_ROUNDS = 5
OVERLAY_CLICK_MS = 2500        # short, so looking for pop-ups adds little time
UPLOAD_WAIT_S = 600            # how long to wait for TikTok to finish processing the video (a Pi uploads slowly)
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
    Yields an _Attempt: started drivers, the action timeout in ms, the light page switches, the screenshots
    taken and the errors the library swallowed.

    It also hooks three of the library's steps. After the upload page loads and after the video has uploaded it
    closes pop-ups. The final Post click is replaced by ours, which closes pop-ups and clicks through Playwright.
    And the whole form is wrapped so a failure inside the library's call always leaves a screenshot."""
    from playwright.sync_api import sync_playwright
    import tiktok_uploader
    import tiktok_uploader.upload as upload_module

    nav_ms, action_ms = browser_helpers.timeouts(cfg)
    started: list = []
    lights: list = []
    sess = _Attempt(started=started, action_ms=action_ms, lights=lights, shots=[], errors=[])

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

    originals = {name: getattr(upload_module, name)
                 for name in ("get_browser", "_go_to_upload", "_set_video", "_post_video", "complete_upload_form")}

    def after(step):
        """The library step, then a sweep for pop-ups."""
        def wrapped(page, *a, **k):
            result = step(page, *a, **k)
            dismiss_overlays(page)
            return result
        return wrapped

    def whole_form(original):
        def wrapped(page, *a, **k):
            try:
                return original(page, *a, **k)
            except Exception as e:  # noqa: BLE001 - the library catches this next and only logs it
                sess.errors.append(e)
                sess.shot(page)
                raise
        return wrapped

    upload_module.get_browser = get_browser
    upload_module._go_to_upload = after(originals["_go_to_upload"])
    upload_module._set_video = after(originals["_set_video"])
    upload_module._post_video = lambda page: _post_with_overlays(page, sess)
    upload_module.complete_upload_form = whole_form(originals["complete_upload_form"])
    try:
        with browser_helpers.library_waits(tiktok_uploader.config, action_ms // 1000):
            yield sess
    finally:
        for name, fn in originals.items():
            setattr(upload_module, name, fn)
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


def _visible(locator) -> bool:
    try:
        return locator.first.is_visible()
    except Exception:  # noqa: BLE001
        return False


def _press(locator) -> bool:
    try:
        locator.first.click(timeout=OVERLAY_CLICK_MS)
        return True
    except Exception:  # noqa: BLE001
        return False


def _rx(key: str):
    return re.compile(SELECTORS[key], re.IGNORECASE)


def _dismiss_once(page) -> str | None:
    """Close one pop-up if one is showing. Returns what it was, or None when nothing was there."""
    S = SELECTORS
    # 1. "Turn on automatic content checks?" Press Cancel inside that modal, never Turn on.
    heading = page.get_by_text(_rx("checks_modal"))
    if _visible(heading):
        box = heading.first.locator("xpath=ancestor::*[.//button[normalize-space(.)='Cancel']][1]")
        cancel = box.get_by_role("button", name=_rx("modal_cancel"))
        if not _visible(cancel):
            cancel = box.get_by_text(_rx("modal_cancel"))
        if _press(cancel):
            return "content checks"
        page.keyboard.press("Escape")  # no Cancel found: Escape declines it too
        return "content checks (Escape)"
    # 2. The tip card with a "Got it" button.
    got_it = page.get_by_role("button", name=_rx("got_it"))
    if not _visible(got_it):
        got_it = page.get_by_text(_rx("got_it"))
    if _visible(got_it):
        label = "new editing features" if _visible(page.get_by_text(_rx("tip_text"))) else "tip"
        if _press(got_it):
            return label
    # 3. Any other dialog or modal: its close button, or Escape.
    dialog = page.locator(S["dialog"])
    if _visible(dialog):
        close = dialog.first.get_by_role("button", name=_rx("close_names"))
        if _visible(close) and not NEVER_PRESS.search(_button_text(close)) and _press(close):
            return "dialog"
        page.keyboard.press("Escape")
        return "dialog (Escape)"
    return None


def _button_text(locator) -> str:
    try:
        return (locator.first.inner_text(timeout=500) or "").strip()
    except Exception:  # noqa: BLE001
        return ""


def dismiss_overlays(page, log=None) -> list[str]:
    """Close the pop-ups TikTok puts over the upload page, up to a few rounds, until none is left.

    Returns what was dismissed. It never turns anything on: the content checks modal is declined with Cancel."""
    if log is None:
        from .. import job

        log = job.log
    found: list[str] = []
    for _ in range(OVERLAY_ROUNDS):
        what = _dismiss_once(page)
        if not what:
            break
        found.append(what)
        log(f"TikTok: dismissed a pop-up ({what})")
        try:
            page.wait_for_timeout(300)  # let it animate away
        except Exception:  # noqa: BLE001
            pass
    return found


def visible_overlay(page) -> str | None:
    """What is still covering the page, or None."""
    S = SELECTORS
    if _visible(page.get_by_text(_rx("checks_modal"))):
        return "the automatic content checks modal"
    if _visible(page.get_by_role("button", name=_rx("got_it"))):
        return "a tip card"
    if _visible(page.locator(S["dialog"])):
        return "a dialog"
    return None


def _post_with_overlays(page, sess) -> None:
    """The last step, replacing tiktok-uploader's own: clear pop-ups, wait until Post is enabled, make sure
    nothing covers it, then click it through Playwright. The library's fallback runs a script that fails with
    'Cannot read properties of null' when an overlay hides the button."""
    from .. import job

    S = SELECTORS
    post = page.locator(S["post_button"])
    dismiss_overlays(page)
    deadline = time.time() + UPLOAD_WAIT_S
    last_sweep = time.time()
    while time.time() < deadline:
        try:
            if post.first.get_attribute(S["post_enabled_attr"], timeout=2000) == "false":
                break
        except Exception:  # noqa: BLE001 - not on the page yet, or mid re-render
            pass
        time.sleep(2)
        if time.time() - last_sweep > 10:  # a pop-up can appear while the video is still processing
            dismiss_overlays(page)
            last_sweep = time.time()
    dismiss_overlays(page)  # right before the click
    left = visible_overlay(page)
    if left:
        raise PlatformError(f"TikTok has {left} covering the Post button that couldn't be closed." + sess.shot(page))
    try:
        post.first.scroll_into_view_if_needed(timeout=sess.action_ms)
        post.first.click(timeout=sess.action_ms)
    except Exception as first:  # noqa: BLE001
        dismiss_overlays(page)
        try:
            post.first.click(timeout=sess.action_ms)
        except Exception:  # noqa: BLE001
            raise PlatformError("TikTok's Post button couldn't be clicked: "
                                + str(first).strip().splitlines()[0][:150] + sess.shot(page)) from None
    try:
        now = page.get_by_role("button", name=_rx("post_now"))
        if _visible(now):
            now.first.click(timeout=OVERLAY_CLICK_MS)
    except Exception:  # noqa: BLE001
        pass
    try:
        page.get_by_text(_rx("post_confirmation")).first.wait_for(state="attached", timeout=sess.action_ms)
    except Exception:  # noqa: BLE001
        raise PlatformError("TikTok didn't confirm that the video was posted." + sess.shot(page)) from None
    job.log("TikTok: Post pressed and confirmed.")


class _Attempt(SimpleNamespace):
    """State of one upload attempt: what the browser session needs and what went wrong in the library."""

    def shot(self, page) -> str:
        """Save a debug screenshot (once per attempt) and return ' (screenshot: data/debug/name)' or ''."""
        if not self.shots:
            name = save_debug_screenshot(page, "tiktok")
            if name:
                self.shots.append(name)
        return f" (screenshot: data/debug/{self.shots[-1]})" if self.shots else ""


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
        with _quiet_library() as messages, _light_browser(cfg) as sess:
            try:
                uploader = TikTokUploader(cookies=str(cookie_store.path_for(self.key)), headless=True, browser="chromium")

                def authenticate(page):
                    try:
                        _check_login(page, sess.action_ms)
                    finally:
                        for light in sess.lights:  # the upload itself gets the full page
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
                    swallowed = sess.errors[-1] if sess.errors else None
                    if isinstance(swallowed, PlatformError):  # ours, with its own clear message
                        raise swallowed
                    why = str(swallowed).strip().splitlines()[0] if swallowed else (messages[-1] if messages else "no reason given")
                    if re.search(r"InsufficientAuth|log ?in", why, re.IGNORECASE):
                        raise CookieExpired(EXPIRED)
                    raise PlatformError("TikTok didn't accept the upload: " + why[:200])
                return "uploaded privately" if private else "uploaded"
            except PlatformError as e:
                self._screenshot(uploader, e, sess)
                raise
            except Exception as e:  # noqa: BLE001
                err = _classify(e) if not isinstance(e, PlatformError) else e
                self._screenshot(uploader, err, sess)
                raise err from None
            finally:
                if uploader is not None:
                    uploader.close()

    def _screenshot(self, uploader, err: Exception, sess) -> None:
        """Every failed attempt leaves a screenshot. One may already exist (taken the moment the library failed,
        or by a navigation timeout), and then that one is named in the message instead of taking another."""
        page = getattr(uploader, "_page", None) if uploader is not None else None
        if page is None and not sess.shots:
            return
        if not sess.shots and not (err.args and "screenshot:" in str(err.args[0])):
            sess.shot(page)
        if isinstance(err, CookieExpired) or (err.args and "screenshot:" in str(err.args[0])) or not sess.shots:
            return  # the expired cookies message stays exactly as the panel shows it
        err.args = (f"{err.args[0]} (screenshot: data/debug/{sess.shots[-1]})",) if err.args else (sess.shots[-1],)
