"""Instagram publishing via instagrapi (unofficial private API, logs in with username + password).

Heads up: this is against Instagram's Terms of Use and can get an account challenged or banned.
Use a throwaway/secondary account, keep posting frequency human-ish, and keep dry_run on until
you've looked at the output.
"""
from pathlib import Path

from .config import DATA

SESSION_FILE = DATA / "instagram_session.json"


def _client(cfg: dict):
    from instagrapi import Client

    user, pw = cfg.get("instagram_username", ""), cfg.get("instagram_password", "")
    if not user or not pw:
        raise RuntimeError("Instagram username/password not set")
    cl = Client()
    cl.delay_range = [1, 3]
    if SESSION_FILE.exists():
        try:
            cl.load_settings(SESSION_FILE)
        except Exception:
            pass
    code = ""
    seed = cfg.get("instagram_2fa_seed", "").replace(" ", "")
    if seed:
        code = cl.totp_generate_code(seed)
    cl.login(user, pw, verification_code=code)
    DATA.mkdir(parents=True, exist_ok=True)
    cl.dump_settings(SESSION_FILE)
    return cl


def post_reel(cfg: dict, video: Path, caption: str) -> str:
    media = _client(cfg).clip_upload(video, caption)
    return str(media.code)


def post_photo(cfg: dict, image: Path, caption: str) -> str:
    media = _client(cfg).photo_upload(image, caption)
    return str(media.code)
