"""What the browser based platforms (TikTok, YouTube) share: timeouts, Chromium flags and light page loading.

A Raspberry Pi 3 has about 1 GB of RAM and a slow CPU, so the defaults of Playwright (30 second navigation,
full "load" event, images and fonts and everything else) are too tight there. Everything here is tuned for the
small machine and is harmless on a big one.
"""
import os
import random
import time
from urllib.parse import urlparse

from .base import Permanent, save_debug_screenshot

DEFAULT_NAV_S = 120      # page.goto
DEFAULT_ACTION_S = 90    # every other wait (clicks, selectors) unless a step has its own
NAV_RETRY_PAUSE = (5, 12)
VIEWPORT = {"width": 1100, "height": 700}

# Low memory flags. --no-sandbox is added only when it is needed, see needs_no_sandbox().
LAUNCH_ARGS = [
    "--disable-dev-shm-usage",     # /dev/shm is tiny on a Pi, so use /tmp
    "--disable-gpu",
    "--disable-extensions",
    "--disable-background-networking",
    "--mute-audio",
    "--disable-blink-features=AutomationControlled",
]

# Not needed to see whether the login worked. Switched off again before anything is uploaded.
HEAVY_RESOURCE_TYPES = {"image", "font", "media"}
ANALYTICS_HOSTS = ("google-analytics.com", "googletagmanager.com", "doubleclick.net", "googlesyndication.com",
                   "connect.facebook.net", "hotjar.com", "clarity.ms", "segment.io", "sentry.io")


class NavigationTimeout(Permanent):
    """A page didn't load even after one more try. Starting the whole upload again won't change that."""


def timeouts(cfg: dict) -> tuple[int, int]:
    """(navigation, action) timeouts in milliseconds, from browser_nav_timeout_s and browser_action_timeout_s."""
    def seconds(key: str, default: int) -> int:
        try:
            return max(10, min(900, int(cfg.get(key) or default)))
        except (TypeError, ValueError):
            return default

    return seconds("browser_nav_timeout_s", DEFAULT_NAV_S) * 1000, seconds("browser_action_timeout_s", DEFAULT_ACTION_S) * 1000


def chrome_user_agent(browser_version: str) -> str:
    """A normal desktop Chrome user agent for the Chromium that is actually running. Headless Chromium announces
    itself as "HeadlessChrome", and YouTube Studio answers that with an "unsupported browser" page."""
    major = (browser_version or "").split(".")[0]
    if not major.isdigit():
        major = "130"
    return (f"Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) "
            f"Chrome/{major}.0.0.0 Safari/537.36")


HIDE_WEBDRIVER = "Object.defineProperty(navigator, 'webdriver', {get: () => undefined});"


def needs_no_sandbox() -> bool:
    """Chromium refuses to start as root without --no-sandbox. A normal user, which is what install.sh sets up,
    keeps the sandbox."""
    return hasattr(os, "geteuid") and os.geteuid() == 0


def launch_args() -> list[str]:
    return LAUNCH_ARGS + (["--no-sandbox"] if needs_no_sandbox() else [])


def should_block(resource_type: str, url: str) -> bool:
    if resource_type in HEAVY_RESOURCE_TYPES:
        return True
    host = (urlparse(url).hostname or "").lower()
    return any(host == h or host.endswith("." + h) for h in ANALYTICS_HOSTS)


class LightPage:
    """Skips images, fonts, media and analytics while the login is being checked. Call off() before the upload,
    so nothing the upload needs is ever blocked."""

    def __init__(self, page):
        self.page = page
        self.active = False

    def _handle(self, route):
        req = route.request
        try:
            if should_block(req.resource_type, req.url):
                route.abort()
            else:
                route.continue_()
        except Exception:  # noqa: BLE001 - the page may already be closing
            pass

    def on(self) -> "LightPage":
        self.page.route("**/*", self._handle)
        self.active = True
        return self

    def off(self) -> None:
        if self.active:
            self.active = False
            try:
                self.page.unroute("**/*", self._handle)
            except Exception:  # noqa: BLE001
                pass


def is_timeout(exc: BaseException) -> bool:
    return type(exc).__name__ == "TimeoutError" or "Timeout" in str(exc)[:60]


def goto(page, url: str, key: str, name: str, nav_ms: int, wait_until: str = "domcontentloaded", call=None, **kw):
    """page.goto that only waits for domcontentloaded, and tries once more after a short pause if the page
    doesn't arrive in time. Before giving up it saves a screenshot to data/debug/."""
    call = call or page.goto
    for attempt in (1, 2):
        try:
            return call(url, wait_until=wait_until, timeout=nav_ms, **kw)
        except Exception as e:  # noqa: BLE001
            if not is_timeout(e):
                raise
            if attempt == 1:
                time.sleep(random.uniform(*NAV_RETRY_PAUSE))
    shot = save_debug_screenshot(page, key)
    raise NavigationTimeout(
        f"{name} didn't load within {nav_ms // 1000} seconds, even after trying again."
        + (f" (screenshot: data/debug/{shot})" if shot else "")
        + " On a Pi 3 you can raise the browser timeouts in the Platforms tab.") from None


def tune_page(page, key: str, name: str, nav_ms: int, action_ms: int) -> None:
    """Longer default timeouts, and make every goto on this page (including the ones TikTok's library makes)
    use domcontentloaded and the retry."""
    page.set_default_timeout(action_ms)
    page.set_default_navigation_timeout(nav_ms)
    raw = page.goto

    def patched(url, **kw):
        kw.pop("timeout", None)
        kw.pop("wait_until", None)
        return goto(page, url, key, name, nav_ms, call=raw, **kw)

    page.goto = patched


def library_waits(module_config, action_s: int):
    """Context manager: raise tiktok-uploader's own waits to at least action_s, then put them back."""
    import contextlib

    @contextlib.contextmanager
    def cm():
        saved = {}
        for field in ("implicit_wait", "explicit_wait"):
            try:
                saved[field] = getattr(module_config, field)
                setattr(module_config, field, max(saved[field], action_s))
            except Exception:  # noqa: BLE001 - not worth failing an upload over
                saved.pop(field, None)
        try:
            yield
        finally:
            for field, value in saved.items():
                try:
                    setattr(module_config, field, value)
                except Exception:  # noqa: BLE001
                    pass

    return cm()
