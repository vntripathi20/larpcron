"""Web control panel: settings, preview, post-now, logs. Runs on 0.0.0.0 so it works over a Pi access point."""
import hmac
import zoneinfo
from datetime import datetime
from zoneinfo import ZoneInfo

from flask import Flask, Response, abort, jsonify, redirect, render_template, request, send_from_directory, url_for

from . import config, job, media, music, scheduler, updates

app = Flask(__name__)


@app.before_request
def _auth():
    pw = config.load()["ui_password"]
    a = request.authorization
    if a and hmac.compare_digest(a.username or "", "admin") and hmac.compare_digest(a.password or "", pw):
        return None
    return Response("Login required (user: admin)", 401, {"WWW-Authenticate": 'Basic realm="LarpCron"'})


def _readiness(cfg: dict) -> list[dict]:
    """Setup checklist on the Home tab: [{ok, title, detail, tab}]."""
    src = cfg["footage_source"]
    local = len(media._local_clips())
    ways = []
    if src in ("online", "pexels", "any") and cfg["pexels_api_key"].strip():
        ways.append("Pexels")
    if src in ("online", "pixabay", "any") and cfg["pixabay_api_key"].strip():
        ways.append("Pixabay")
    if src in ("tiktok", "any") and cfg["tiktok_profile"].strip():
        ways.append("TikTok")
    if src in ("local", "any") and local:
        ways.append(f"{local} clips of your own")
    ig = bool(cfg["instagram_username"] and cfg["instagram_password"])
    n_links = len(cfg["music_links"])
    return [
        {"ok": bool(ways), "tab": "content", "title": "Footage",
         "detail": "Pulling from " + ", ".join(ways) if ways
         else "Nothing to pull from yet. Add a key, a TikTok profile, or put clips in assets/backgrounds."},
        {"ok": True, "tab": "music", "title": "Music",
         "detail": f"{n_links} YouTube link{'s' if n_links != 1 else ''} saved." if n_links
         else "None added, so reels will be silent."},
        {"ok": ig, "tab": "accounts", "title": "Instagram login",
         "detail": f"Saved for @{cfg['instagram_username']}." if ig
         else "Not set. You only need it to post for real."},
        {"ok": not cfg["dry_run"], "tab": "schedule", "title": "Posting",
         "detail": "Dry run is off, so posts go to Instagram." if not cfg["dry_run"]
         else "Dry run is on, so nothing gets posted."},
        {"ok": cfg["enabled"], "tab": "schedule", "title": "Schedule",
         "detail": f"{scheduler.describe(cfg)}." if cfg["enabled"] else "Automatic posting is off."},
    ]


def _status() -> dict:
    outs = sorted([*config.OUT.glob("*-reel.mp4"), *config.OUT.glob("*-post.jpg")],
                  key=lambda p: p.stat().st_mtime, reverse=True)
    hist = job.history()
    last = job.STATE["last_result"] or (hist[-1] if hist else {})
    latest = outs[0].name if outs else None
    kind = "photo" if latest and latest.endswith(".jpg") else "reel"
    poster = latest[:-4] + ".jpg" if kind == "reel" and latest and (config.OUT / (latest[:-4] + ".jpg")).exists() else None
    return {
        "busy": job.STATE["busy"], "error": last.get("error"), "latest": latest, "kind": kind, "poster": poster,
        "log": list(job.LOG)[::-1], "next_runs": [scheduler.format_slot(d) for d in scheduler.upcoming(config.load(), 3)] if config.load()["enabled"] else [],
        "quote": last.get("quote"), "author": last.get("author"), "source": last.get("source"),
    }


@app.get("/")
def index():
    cfg = config.load()
    return render_template(
        "index.html", cfg=cfg, st=_status(), hist=job.history()[::-1][:8],
        checks=_readiness(cfg), timezones=sorted(zoneinfo.available_timezones()),
        upcoming=[scheduler.format_slot(d) for d in scheduler.upcoming(cfg, 6)], summary=scheduler.describe(cfg),
        up=updates.info(),
    )


@app.get("/status")
def status():
    resp = jsonify(_status())
    resp.headers["Cache-Control"] = "no-store"
    return resp


@app.post("/settings")
def settings():
    cfg = config.load()
    f = request.form
    for k in ("instagram_username", "pexels_api_key", "pixabay_api_key", "tiktok_profile", "timezone", "hashtags", "caption_template", "watermark"):
        cfg[k] = f.get(k, "").strip()
    # Secrets: blank field = keep existing value.
    for k in ("instagram_password", "instagram_2fa_seed", "ui_password"):
        if f.get(k, "").strip():
            cfg[k] = f[k].strip()
    cfg["enabled"] = "enabled" in f
    cfg["dry_run"] = "dry_run" in f
    cfg["smart_music_start"] = "smart_music_start" in f
    cfg["auto_update"] = "auto_update" in f
    cfg["content_mode"] = f.get("content_mode", "reel") if f.get("content_mode") in ("reel", "photo", "mix") else "reel"
    cfg["footage_source"] = f.get("footage_source") if f.get("footage_source") in ("any", "online", "pexels", "pixabay", "tiktok", "local") else "any"
    cfg["quote_source"] =f.get("quote_source", "mixed") if f.get("quote_source") in ("local", "online", "mixed") else "mixed"
    cfg["reel_seconds"] = max(5, min(60, int(f.get("reel_seconds") or 9)))
    cfg["themes"] = [t.strip() for t in f.get("themes", "").splitlines() if t.strip()]
    links = [ln.strip() for ln in f.get("music_links", "").splitlines() if ln.strip()]
    for ln in links:
        if music.is_spotify(ln):
            job.log("Spotify links can't be used (no audio access). Use a YouTube link: " + ln[:60])
        elif not music.parse_line(ln):
            job.log("Ignoring music line (not a YouTube link): " + ln[:60])
    cfg["music_links"] = links
    times = scheduler.parse_times(f.get("post_times", ""))
    cfg["post_times"] = times or cfg["post_times"]

    def whole(name, lo, hi, keep):
        try:
            return max(lo, min(hi, int(f.get(name, ""))))
        except ValueError:
            return keep

    cfg["schedule_mode"] = f.get("schedule_mode") if f.get("schedule_mode") in ("weekdays", "interval") else cfg["schedule_mode"]
    if "days_form" in f:  # unchecked boxes are simply absent, so only trust them when the day block was sent
        cfg["post_days"] = sorted({int(d) for d in f.getlist("post_days") if d.isdigit() and 0 <= int(d) <= 6})
        if not cfg["post_days"]:
            job.log("No posting days are ticked, so nothing will post until you pick at least one.")
    cfg["every_n_days"] = whole("every_n_days", 1, 30, cfg["every_n_days"])
    cfg["time_jitter_min"] = whole("time_jitter_min", 0, 120, cfg["time_jitter_min"])
    cfg["post_chance"] = whole("post_chance", 1, 100, cfg["post_chance"])
    anchor = scheduler.parse_date(f.get("interval_anchor", ""))
    cfg["interval_anchor"] = anchor.isoformat() if anchor else (cfg["interval_anchor"] or "")
    pause = scheduler.parse_date(f.get("pause_until", ""))
    cfg["pause_until"] = pause.isoformat() if pause else ""
    try:
        ZoneInfo(cfg["timezone"])
    except Exception:
        cfg["timezone"] = "UTC"
        job.log("Unknown timezone, falling back to UTC")
    if cfg["schedule_mode"] == "interval" and not cfg["interval_anchor"]:
        cfg["interval_anchor"] = datetime.now(ZoneInfo(cfg["timezone"])).date().isoformat()
    config.save(cfg)
    scheduler.reschedule(cfg)
    run = request.args.get("run")
    if run in ("preview", "post"):
        job.run_async(post=(run == "post"))
    return redirect(url_for("index"))


@app.get("/update/status")
def update_status():
    resp = jsonify(updates.live())
    resp.headers["Cache-Control"] = "no-store"
    return resp


@app.post("/update/run")
def update_run():
    started, message = updates.request_update()
    return jsonify({"ok": started, "message": message}), (200 if started else 409)


@app.post("/generate")
def generate():
    job.run_async(post=False)
    return redirect(url_for("index"))


@app.post("/post-now")
def post_now():
    job.run_async(post=True)
    return redirect(url_for("index"))


@app.get("/media/<path:name>")
def media_file(name):
    if "/" in name or "\\" in name:
        abort(404)
    return send_from_directory(config.OUT, name)
