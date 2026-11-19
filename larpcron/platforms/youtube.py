"""YouTube Shorts through YouTube Studio, driven by Playwright with exported browser cookies.

There is no API here, so this clicks through the Studio upload dialog the way a person would. YouTube
changes that page now and then. When an upload fails, a screenshot lands in data/debug/ and the selector
that no longer matches is in SELECTORS below. That dict is the only place that knows what Studio looks like.

Automated uploads are against YouTube's terms and can get a channel flagged. Use one you can lose.
"""
import time
from pathlib import Path

from . import browser as browser_helpers
from . import cookies as cookie_store
from .base import (CookieExpired, Permanent, Platform, PlatformError, probe_video, save_debug_screenshot,
                   with_retry)

# ---- everything that depends on YouTube's page lives here --------------------------------------------
SELECTORS = {
    "studio_url": "https://studio.youtube.com/",
    "logged_in_marker": "#create-icon",                          # the Create button, only there when signed in
    "create_button": "#create-icon",
    "upload_menu_item": "#text-item-0",                          # "Upload videos" in the Create menu
    "file_input": "input[type='file']",
    "title_box": "#title-textarea #textbox",
    "description_box": "#description-textarea #textbox",
    "not_for_kids": "#audience [name='VIDEO_MADE_FOR_KIDS_NOT_MFK']",
    "next_button": "#next-button",
    "visibility": {"public": "[name='PUBLIC']", "unlisted": "[name='UNLISTED']", "private": "[name='PRIVATE']"},
    "done_button": "#done-button",
    "upload_progress": "ytcp-video-upload-progress",
    "published_close": "#close-button",                        # "Video published" dialog
    "video_link": "ytcp-video-share-dialog a[href*='youtu'], .video-url-fadeable a",
}
SIGNED_OUT_URL_PARTS = ("accounts.google.com", "ServiceLogin", "/signin")
NEXT_CLICKS = 3                 # Details, Video elements, Checks, then the Visibility page
STEP_TIMEOUT_MS = 120_000       # used when a caller doesn't pass its own (the config sets the real ones)
UPLOAD_TIMEOUT_MS = 20 * 60_000  # waiting for the file to finish uploading and the checks to pass
# -------------------------------------------------------------------------------------------------------

DOMAINS = ("youtube.com", "google.com")
LOGIN_COOKIES = ("SID", "SAPISID", "__Secure-1PSID", "__Secure-3PSID", "LOGIN_INFO")
EXPIRED = "YouTube cookies expired. Re-export from a logged-in browser."
NO_BROWSER = "Chromium isn't installed for Playwright. Run deploy/install-browser.sh on this machine."
MAX_TITLE = 100
MAX_SECONDS = 60
VISIBILITIES = ("public", "unlisted", "private")


def split_caption(caption: str) -> tuple[str, str]:
    """make_caption() hands over 'title, blank line, description'."""
    title, _, description = caption.partition("\n\n")
    return title.strip(), description.strip()


def with_shorts_tag(title: str, description: str) -> tuple[str, str]:
    title = " ".join(title.split())
    if "#shorts" not in title.lower():
        title = (title[: MAX_TITLE - len(" #Shorts")].rstrip() + " #Shorts")
    title = title[:MAX_TITLE]
    if "#shorts" not in description.lower():
        description = (description + "\n\n#Shorts").strip()
    return title, description


def check_short(path: Path) -> None:
    wide, high, seconds = probe_video(path)
    if high <= wide:
        raise PlatformError("A YouTube Short has to be vertical.")
    if seconds >= MAX_SECONDS:
        raise PlatformError(f"This video is {seconds:.0f} seconds. A YouTube Short has to be under {MAX_SECONDS}. "
                            "Lower the reel length in the Content tab.")


def _step(state: dict, name: str) -> None:
    state["step"] = name


def upload_flow(page, video: Path, title: str, description: str, visibility: str, state: dict,
                step_ms: int = STEP_TIMEOUT_MS, nav_ms: int = STEP_TIMEOUT_MS, light=None) -> str:
    """The Studio upload dialog, start to finish. Returns the video link if Studio shows one.

    step_ms is the wait for each click or selector, nav_ms the wait for a page to load. light, if given, is
    the switch that blocks images and fonts until the login has been checked."""
    S = SELECTORS
    _step(state, "opening Studio")
    try:
        browser_helpers.goto(page, S["studio_url"], "youtube", "YouTube Studio", nav_ms)
        try:
            page.wait_for_selector(S["logged_in_marker"], timeout=step_ms)
        except Exception:  # noqa: BLE001
            if any(part in page.url for part in SIGNED_OUT_URL_PARTS):
                raise CookieExpired(EXPIRED) from None
            raise PlatformError("YouTube Studio didn't load (no Create button). It may be asking for a check by hand.") from None
    finally:
        if light is not None:
            light.off()  # the upload itself gets the whole page
    if any(part in page.url for part in SIGNED_OUT_URL_PARTS):
        raise CookieExpired(EXPIRED)

    _step(state, "opening the upload dialog")
    page.click(S["create_button"], timeout=step_ms)
    page.click(S["upload_menu_item"], timeout=step_ms)
    page.wait_for_selector(S["file_input"], state="attached", timeout=step_ms)

    _step(state, "choosing the file")
    page.set_input_files(S["file_input"], str(video), timeout=step_ms)

    _step(state, "filling in the title and description")
    page.wait_for_selector(S["title_box"], timeout=step_ms)
    page.fill(S["title_box"], title, timeout=step_ms)
    page.fill(S["description_box"], description, timeout=step_ms)

    _step(state, "marking it not made for kids")
    page.click(S["not_for_kids"], timeout=step_ms)

    _step(state, "going through the dialog")
    for _ in range(NEXT_CLICKS):
        page.click(S["next_button"], timeout=step_ms)
        time.sleep(1.5)

    _step(state, "choosing " + visibility)
    page.click(S["visibility"][visibility], timeout=step_ms)

    _step(state, "waiting for the upload and checks to finish")
    page.wait_for_function(
        "sel => { const b = document.querySelector(sel); return b && b.getAttribute('aria-disabled') !== 'true' && !b.hasAttribute('disabled'); }",
        arg=S["done_button"], timeout=UPLOAD_TIMEOUT_MS)

    _step(state, "publishing")
    page.click(S["done_button"], timeout=step_ms)
    link = ""
    try:
        page.wait_for_selector(S["published_close"], timeout=step_ms)
        found = page.query_selector(S["video_link"])
        link = (found.get_attribute("href") if found else "") or ""
        page.click(S["published_close"], timeout=10_000)
    except Exception:  # noqa: BLE001 - Done was clicked, so the video is on its way even if the dialog differs
        pass
    return link


class YouTube(Platform):
    key = "youtube"
    name = "YouTube"
    kinds = ("reel",)
    browser = True
    can_test = True
    cookie_domains = DOMAINS
    cookie_names = LOGIN_COOKIES

    def enabled(self, cfg: dict) -> bool:
        return bool(cfg.get("youtube_enabled"))

    def make_caption(self, cfg: dict, quote: str, author: str, default: str) -> str:
        template = (cfg.get("youtube_title_template") or "{quote}").strip() or "{quote}"
        try:
            title = template.format(quote=quote, author=author, hashtags=cfg.get("hashtags", ""))
        except (KeyError, IndexError, ValueError):
            title = quote
        title, description = with_shorts_tag(title, default)
        return f"{title}\n\n{description}"

    def post(self, video_path: Path, caption: str, cfg: dict, private: bool = False) -> str:
        from .. import job

        if not cookie_store.exists(self.key):
            raise CookieExpired("YouTube has no cookies saved. Paste an export in the Platforms tab.")
        check_short(video_path)
        title, description = with_shorts_tag(*split_caption(caption))
        visibility = "private" if private else (cfg.get("youtube_visibility") if cfg.get("youtube_visibility") in VISIBILITIES else "public")
        return with_retry(lambda: self._upload_once(video_path, title, description, visibility, cfg), log=job.log)

    def _upload_once(self, video: Path, title: str, description: str, visibility: str, cfg: dict) -> str:
        try:
            from playwright.sync_api import sync_playwright
        except ImportError:
            raise Permanent("Playwright isn't installed. Run: pip install -r requirements.txt") from None

        state = {"step": "starting the browser"}
        nav_ms, action_ms = browser_helpers.timeouts(cfg)
        pw = browser = page = None
        try:
            pw = sync_playwright().start()
            browser = pw.chromium.launch(headless=True, args=browser_helpers.launch_args())
            context = browser.new_context(viewport=browser_helpers.VIEWPORT, locale="en-US")
            context.add_cookies(cookie_store.to_playwright(cookie_store.load(self.key)))
            page = context.new_page()
            page.set_default_timeout(action_ms)
            page.set_default_navigation_timeout(nav_ms)
            light = browser_helpers.LightPage(page).on()  # no images, fonts, media or analytics while logging in
            link = upload_flow(page, video, title, description, visibility, state, step_ms=action_ms, nav_ms=nav_ms, light=light)
            return link or f"uploaded ({visibility})"
        except PlatformError as e:
            self._screenshot(page, e)
            raise
        except Exception as e:  # noqa: BLE001
            text = str(e)
            if "Executable doesn't exist" in text or "playwright install" in text:
                raise Permanent(NO_BROWSER) from None
            err = PlatformError(f"YouTube upload failed while {state['step']}: {text.strip().splitlines()[0][:200]}")
            self._screenshot(page, err)
            raise err from None
        finally:
            for closer in (browser and browser.close, pw and pw.stop):  # never leave Chromium running
                if closer:
                    try:
                        closer()
                    except Exception:  # noqa: BLE001
                        pass

    def _screenshot(self, page, err: Exception) -> None:
        if isinstance(err, CookieExpired) or page is None:
            return
        name = save_debug_screenshot(page, self.key)
        if name:
            err.args = (f"{err.args[0]} (screenshot: data/debug/{name})",)
