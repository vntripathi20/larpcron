"""Browser cookie exports, for the platforms that post through a logged-in browser session.

People paste an export from a browser extension, either Netscape cookies.txt or JSON. It is cleaned up,
reduced to the cookies of the platform's own sites, and saved as a Netscape file in data/ that only
the owner can read (mode 600). Cookie values are never logged and never sent back to the page.
"""
import json
import os
from pathlib import Path

from .. import config

MAX_EXPORT_BYTES = 1_000_000
_SAMESITE = {"lax": "Lax", "strict": "Strict", "no_restriction": "None", "none": "None"}


def path_for(key: str) -> Path:
    return config.DATA / f"{key}_cookies.txt"


def exists(key: str) -> bool:
    try:
        return path_for(key).stat().st_size > 0
    except OSError:
        return False


def _expires(value) -> float:
    try:
        v = float(value)
    except (TypeError, ValueError):
        return -1
    return v if v > 0 else -1  # session cookies come as 0, -1 or nothing


def _from_json(text: str) -> list[dict]:
    data = json.loads(text)
    if isinstance(data, dict):  # Playwright storage state, or {"cookies": [...]}
        data = data.get("cookies", [])
    out = []
    for c in data if isinstance(data, list) else []:
        if not isinstance(c, dict) or not c.get("name") or "value" not in c or not c.get("domain"):
            continue
        item = {"name": str(c["name"]), "value": str(c["value"]), "domain": str(c["domain"]),
                "path": str(c.get("path") or "/"),
                "expires": _expires(c.get("expirationDate", c.get("expires", c.get("expiry")))),
                "secure": bool(c.get("secure")), "httpOnly": bool(c.get("httpOnly"))}
        same = _SAMESITE.get(str(c.get("sameSite", "")).lower())
        if same:
            item["sameSite"] = same
        out.append(item)
    return out


def _from_netscape(text: str) -> list[dict]:
    out = []
    for raw in text.splitlines():
        line = raw.strip()
        http_only = line.startswith("#HttpOnly_")
        if http_only:
            line = line[len("#HttpOnly_"):]
        elif not line or line.startswith("#"):
            continue
        parts = line.split("\t")
        if len(parts) < 7:
            parts = line.split(None, 6)  # a paste that turned the tabs into spaces
        if len(parts) < 7:
            continue
        domain, _, path, secure, expires, name, value = (p.strip() for p in parts[:7])
        out.append({"name": name, "value": value, "domain": domain, "path": path or "/",
                    "expires": _expires(expires), "secure": secure.upper() == "TRUE", "httpOnly": http_only})
    return out


def parse(text: str) -> list[dict]:
    """Cookies from a pasted export, in either format. Returns [] when nothing in it looks like cookies."""
    text = (text or "").strip().lstrip("﻿")
    if text[:1] in "[{":
        try:
            return _from_json(text)
        except ValueError:
            return []
    return _from_netscape(text)


def save(key: str, text: str, domains: tuple, required: tuple) -> int:
    """Keep only the cookies for `domains`, check one of `required` names is there, write the file (mode 600).
    Returns how many cookies were saved. Raises ValueError with a message for the person."""
    if len((text or "").encode("utf-8")) > MAX_EXPORT_BYTES:
        raise ValueError("That cookie export is too big. Export only the site you post to.")
    cookies = parse(text)
    if not cookies:
        raise ValueError("Couldn't find any cookies in that. Paste a Netscape cookies.txt or a JSON export.")
    mine = [c for c in cookies if any(c["domain"].lstrip(".").endswith(d) for d in domains)]
    if not mine:
        raise ValueError("None of those cookies are for " + " or ".join(domains) + ". Export them while on that site.")
    if required and not any(c["name"] in required for c in mine):
        raise ValueError("The export has no login cookie (" + " or ".join(required) + "). Log in first, then export again.")
    lines = ["# Netscape HTTP Cookie File"]
    for c in mine:
        lines.append("\t".join([c["domain"], "TRUE" if c["domain"].startswith(".") else "FALSE", c["path"],
                                "TRUE" if c["secure"] else "FALSE", str(int(c["expires"])), c["name"], c["value"]]))
    dest = path_for(key)
    config.DATA.mkdir(parents=True, exist_ok=True)
    fd = os.open(dest, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as fh:
        fh.write("\n".join(lines) + "\n")
    try:
        os.chmod(dest, 0o600)  # in case the file already existed with looser rights
    except OSError:
        pass
    return len(mine)


def load(key: str) -> list[dict]:
    try:
        return _from_netscape(path_for(key).read_text(encoding="utf-8"))
    except OSError:
        return []


def to_playwright(cookies: list[dict]) -> list[dict]:
    out = []
    for c in cookies:
        item = {"name": c["name"], "value": c["value"], "domain": c["domain"], "path": c["path"],
                "secure": c["secure"], "httpOnly": c["httpOnly"], "expires": c["expires"]}
        if c.get("sameSite"):
            item["sameSite"] = c["sameSite"]
        out.append(item)
    return out
