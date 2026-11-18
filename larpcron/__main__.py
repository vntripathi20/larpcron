import argparse
import sys
from pathlib import Path

from . import config, job, scheduler
from .web import app


def split_cmd(argv: list[str]) -> None:
    from .split import split

    ap = argparse.ArgumentParser(prog="larpcron split", description="Cut a compilation into clips")
    ap.add_argument("video", type=Path)
    ap.add_argument("--threshold", type=float, default=0.35, help="scene-change sensitivity (lower = more cuts)")
    ap.add_argument("--min", type=float, default=3.0, dest="min_len")
    ap.add_argument("--max", type=float, default=15.0, dest="max_len")
    a = ap.parse_args(argv)
    n = split(a.video, a.threshold, a.min_len, a.max_len)
    print(f"Wrote {n} clips to assets/backgrounds/")


def main() -> None:
    if len(sys.argv) > 1 and sys.argv[1] == "split":
        return split_cmd(sys.argv[2:])
    ap = argparse.ArgumentParser(prog="larpcron", description="Scheduled quote reels for Instagram")
    ap.add_argument("--host", default="0.0.0.0", help="bind address (default: all interfaces)")
    ap.add_argument("--port", type=int, default=8080)
    ap.add_argument("--once", action="store_true", help="render one reel now (no posting) and exit")
    args = ap.parse_args()

    config.ensure_dirs()
    if args.once:
        job.run_once(post=False)
        return
    scheduler.start()
    scheduler.reschedule(config.load())
    print(f"LarpCron panel on http://{args.host}:{args.port}  (user: admin)")
    app.run(host=args.host, port=args.port, threaded=True)


if __name__ == "__main__":
    main()
