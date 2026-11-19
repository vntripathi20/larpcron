"""Posting platforms. Instagram is always there; TikTok and YouTube Shorts are optional and off by default.

Each platform is a small class (see base.py). To add one, write the class and list it in all_platforms().
The browser based ones import Playwright only when they post, so nothing heavy loads at startup.
"""
from .base import CookieExpired, Platform, PlatformError, with_retry


def all_platforms() -> list[Platform]:
    from .instagram import Instagram

    return [Instagram()]


def get(key: str) -> Platform | None:
    return next((p for p in all_platforms() if p.key == key), None)


def enabled_platforms(cfg: dict) -> list[Platform]:
    return [p for p in all_platforms() if p.enabled(cfg)]
