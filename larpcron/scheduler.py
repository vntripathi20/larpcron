"""APScheduler wrapper. A job fires every day at each configured time; the day rules are checked
when it fires (weekday / every-N-days / pause / chance), which keeps daylight saving changes harmless."""
import random
import re
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger

from . import config, job

_sched = BackgroundScheduler()
TIME_RE = re.compile(r"^([01]?\d|2[0-3]):([0-5]\d)$")
DAY_NAMES = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]


def parse_times(text: str) -> list[str]:
    out = []
    for part in re.split(r"[,\s]+", text.strip()):
        m = TIME_RE.match(part)
        if m:
            out.append(f"{int(m.group(1)):02d}:{m.group(2)}")
    return sorted(set(out))


def parse_date(value) -> date | None:
    try:
        return date.fromisoformat(str(value).strip())
    except ValueError:
        return None


def day_is_active(cfg: dict, d: date) -> bool:
    """Is `d` a day LarpCron should post on? (Ignores the random per-slot chance.)"""
    paused_until = parse_date(cfg.get("pause_until", ""))
    if paused_until and d <= paused_until:
        return False
    if cfg.get("schedule_mode") == "interval":
        anchor = parse_date(cfg.get("interval_anchor", "")) or d
        return (d - anchor).days % max(1, int(cfg.get("every_n_days", 1))) == 0
    return d.weekday() in cfg.get("post_days", range(7))


def describe(cfg: dict) -> str:
    """One plain sentence summing up the schedule."""
    if cfg.get("schedule_mode") == "interval":
        n = int(cfg["every_n_days"])
        when = "every day" if n == 1 else f"every {n} days"
    else:
        days = sorted(set(cfg["post_days"]))
        if len(days) == 7:
            when = "every day"
        elif days == [0, 1, 2, 3, 4]:
            when = "on weekdays"
        elif days == [5, 6]:
            when = "on weekends"
        elif not days:
            when = "on no days (pick at least one)"
        else:
            when = "on " + ", ".join(DAY_NAMES[i] for i in days)
    text = f"{', '.join(cfg['post_times'])} {when}"
    if cfg.get("time_jitter_min"):
        text += f", give or take {cfg['time_jitter_min']} min"
    if int(cfg.get("post_chance", 100)) < 100:
        text += f", {cfg['post_chance']}% chance each time"
    paused = parse_date(cfg.get("pause_until", ""))
    if paused and paused >= datetime.now(ZoneInfo(cfg["timezone"])).date():
        text += f", paused until {paused.strftime('%b %d')}"
    return f"{text} ({cfg['timezone']})"


def upcoming(cfg: dict, limit: int = 6, days_ahead: int = 120) -> list[datetime]:
    """The next posting slots, ignoring the random chance and jitter (those happen at run time)."""
    tz = ZoneInfo(cfg["timezone"])
    now = datetime.now(tz)
    out: list[datetime] = []
    for i in range(days_ahead):
        d = now.date() + timedelta(days=i)
        if not day_is_active(cfg, d):
            continue
        for t in cfg["post_times"]:
            hh, mm = t.split(":")
            slot = datetime(d.year, d.month, d.day, int(hh), int(mm), tzinfo=tz)
            if slot > now:
                out.append(slot)
        if len(out) >= limit:
            break
    return out[:limit]


def format_slot(dt: datetime) -> str:
    return dt.strftime("%a %b %d, %H:%M")


def scheduled_run() -> None:
    """What each scheduled slot runs: check the day rules and the chance, then post."""
    cfg = config.load()
    today = datetime.now(ZoneInfo(cfg["timezone"])).date()
    if not day_is_active(cfg, today):
        job.log("Scheduled slot skipped: today is not a posting day.")
        return
    chance = max(1, min(100, int(cfg.get("post_chance", 100))))
    if random.random() * 100 >= chance:
        job.log(f"Scheduled slot skipped by the {chance}% post chance.")
        return
    job.run_once(True)


def start() -> None:
    if not _sched.running:
        _sched.start()


def reschedule(cfg: dict) -> None:
    for j in _sched.get_jobs():
        j.remove()
    if not cfg["enabled"]:
        return
    jitter = int(cfg.get("time_jitter_min", 0)) * 60 or None
    for t in cfg["post_times"]:
        hh, mm = t.split(":")
        _sched.add_job(
            scheduled_run, CronTrigger(hour=int(hh), minute=int(mm), timezone=cfg["timezone"], jitter=jitter),
            id=f"post-{t}", misfire_grace_time=600, coalesce=True,
        )
    job.log("Scheduler armed: " + describe(cfg))
