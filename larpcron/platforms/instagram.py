"""Instagram as a platform. All the real work is in larpcron/instagram.py, unchanged."""
from pathlib import Path

from .. import instagram
from .base import Platform


class Instagram(Platform):
    key = "instagram"
    name = "Instagram"
    kinds = ("reel", "photo")

    def enabled(self, cfg: dict) -> bool:
        return True  # as before: Instagram is what "post" means. Dry run and missing logins are handled upstream.

    def link(self, ref: str) -> str:
        return f"https://www.instagram.com/p/{ref}/"

    def post(self, video_path: Path, caption: str, cfg: dict, private: bool = False) -> str:
        if video_path.suffix.lower() == ".mp4":
            return instagram.post_reel(cfg, video_path, caption)
        return instagram.post_photo(cfg, video_path, caption)
