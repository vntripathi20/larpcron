"""Last result per platform, kept in data/platform_status.json so the panel can show it."""
import json
import time

from .. import config

STATUS_FILE = config.DATA / "platform_status.json"


def all_status() -> dict:
    try:
        data = json.loads(STATUS_FILE.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def record(key: str, ok: bool, text: str) -> None:
    """Remember the outcome. A success and an error are kept separately, so a later success doesn't hide
    the last error and the other way round."""
    data = all_status()
    entry = data.get(key, {})
    entry["ok" if ok else "error"] = {"time": time.strftime("%Y-%m-%d %H:%M:%S"), "text": text[:300]}
    entry["last"] = "ok" if ok else "error"
    data[key] = entry
    try:
        config.DATA.mkdir(parents=True, exist_ok=True)
        tmp = STATUS_FILE.with_suffix(".tmp")
        tmp.write_text(json.dumps(data, indent=2), encoding="utf-8")
        tmp.replace(STATUS_FILE)
    except OSError:
        pass
