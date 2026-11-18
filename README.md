# LarpCron

It makes quote reels over luxury car, Miami and city footage, then posts them to Instagram on a schedule so you don't have to. You run the whole thing from a small web panel. Put it on a Raspberry Pi that doubles as a Wi-Fi hotspot and you can control it from your phone.

```
footage (stock APIs / TikTok creator / your clips) + quote + music
        ->  ffmpeg 1080x1920 reel  ->  Instagram, at the times you pick
```

## Contents

- [Getting it running](#getting-it-running)
  - [What you need](#what-you-need)
  - [Windows](#windows)
  - [macOS](#macos)
  - [Linux](#linux)
  - [Raspberry Pi](#raspberry-pi)
- [First run](#first-run)
- [Posting on a schedule](#posting-on-a-schedule)
- [Updating](#updating)
- [When stuff breaks](#when-stuff-breaks)
- [Footage](#footage)
- [Music](#music)
- [Every setting](#every-setting)
- [Before you post for real](#before-you-post-for-real)
- [What's where](#whats-where)
- [Credits](#credits)

---

## Getting it running

### What you need

| Thing | Notes |
|---|---|
| Python 3.10 or newer | I built it on 3.11. Check with `python --version` (or `python3 --version`). |
| git | For cloning the repo. You can grab the ZIP from GitHub instead if you'd rather. |
| ffmpeg | Comes bundled through the `imageio-ffmpeg` package on Windows, macOS and 64-bit Linux/Pi. If there's already an `ffmpeg` on your PATH, it uses that one instead. On 32-bit Raspberry Pi OS you'll need to install it yourself (`sudo apt install ffmpeg`). |
| Internet | For footage, quotes, music, and the actual posting. |
| Free API keys (optional) | [Pexels](https://www.pexels.com/api/) and/or [Pixabay](https://pixabay.com/api/docs/) for stock footage. Skip them if you're using the TikTok creator source or your own clips. |
| An Instagram account | Only for real posting. Use one you can live without (more on that [below](#before-you-post-for-real)). |

Disk space: a few hundred MB for the install, plus whatever the cache grows to in `data/`. The footage cache only keeps the 15 newest clips, but reels in `data/out/` stick around until you delete them.

### Windows

Open PowerShell:

```powershell
git clone https://github.com/vntripathi20/LarpCron.git
cd LarpCron
python -m venv .venv
.venv\Scripts\Activate.ps1
pip install -r requirements.txt
python -m larpcron
```

- If activation gets blocked with "running scripts is disabled", run `Set-ExecutionPolicy -Scope Process Bypass` once in that window and try again.
- If `python` isn't found, install it from <https://www.python.org/downloads/> and tick Add python.exe to PATH, or just use `py -3` instead of `python`.
- Then open <http://127.0.0.1:8080> in your browser. Leave the PowerShell window open, because closing it stops LarpCron and the scheduler with it.
- Want it running in the background? Make a Task Scheduler task that runs `C:\path\to\LarpCron\.venv\Scripts\python.exe -m larpcron` at log on, with "Start in" pointed at the LarpCron folder.

### macOS

```bash
brew install python git            # skip what you already have
git clone https://github.com/vntripathi20/LarpCron.git
cd LarpCron
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
python -m larpcron
```

Open <http://127.0.0.1:8080>. Keep the terminal open, or run it under `tmux` or `launchd` if you want it to survive logging out.

### Linux

```bash
# Debian/Ubuntu example; use your distro's equivalents
sudo apt update
sudo apt install -y python3 python3-venv python3-pip git ffmpeg fonts-dejavu-core
git clone https://github.com/vntripathi20/LarpCron.git
cd LarpCron
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
python -m larpcron
```

`fonts-dejavu-core` is there so the quote text gets a proper font. With no fonts installed it falls back to a tiny default that looks bad. To keep it running all the time, use the systemd steps in the Pi section below, which work on any systemd Linux.

### Raspberry Pi

This is what the project was built for. The Pi sits plugged in somewhere, posts on its own, and you poke at it from your phone when you want to change something.

#### 1. What you'll need

- A Pi 4 or 5 is the sweet spot. A Pi 3 or Zero 2 W works too, it's just slow at rendering (timings further down).
- A microSD card (16 GB or more) and a power supply.
- Internet for the Pi. If you turn it into a hotspot (step 5), its Wi-Fi is busy being the hotspot, so plug in Ethernet or add a second USB Wi-Fi adapter for downloads and posting.

#### 2. Flash the OS

1. Install Raspberry Pi Imager on your computer.
2. Pick Raspberry Pi OS Lite (64-bit), Bookworm or newer. It has to be 64-bit, since the bundled ffmpeg only ships 64-bit ARM builds.
3. Hit the gear icon / "Edit settings" and set a hostname (like `larpcron`), a username and password, your home Wi-Fi (just for setup), and turn on SSH.
4. Write the card, stick it in the Pi, and power it on.

#### 3. Connect and install

```bash
ssh <your-user>@larpcron.local        # or use the Pi's IP address from your router
sudo apt update && sudo apt full-upgrade -y
sudo apt install -y python3-venv python3-pip git ffmpeg fonts-dejavu-core nodejs

git clone https://github.com/vntripathi20/LarpCron.git
cd LarpCron
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt        # several minutes on a Pi: be patient
python -m larpcron                     # quick test; Ctrl+C to stop
```

From another device on the same network, open `http://larpcron.local:8080` (or `http://<pi-ip>:8080`, and `hostname -I` will tell you the IP). `nodejs` is only in there so yt-dlp can handle YouTube music links, so skip it if you won't use those.

#### 4. Run it at boot (systemd)

The included unit file assumes the user `pi` and the folder `/home/pi/LarpCron`. This command swaps in your own user and path while it installs the file:

```bash
cd ~/LarpCron
sed "s|/home/pi|$HOME|g; s|User=pi|User=$USER|" deploy/larpcron.service | sudo tee /etc/systemd/system/larpcron.service
sudo systemctl daemon-reload
sudo systemctl enable --now larpcron
```

Handy ones to know:

```bash
systemctl status larpcron          # is it running?
journalctl -u larpcron -f          # live logs
sudo systemctl restart larpcron    # after changing code or updating
```

#### 5. Turn the Pi into a Wi-Fi hotspot (optional)

This needs Raspberry Pi OS Bookworm or newer, because it uses NetworkManager's `nmcli`.

```bash
bash deploy/hotspot.sh LarpCron 'a-strong-password'     # password must be 8+ characters
```

Now join the `LarpCron` Wi-Fi from your phone and open `http://10.42.0.1:8080`. The hotspot comes back on its own after a reboot. Since the Pi's Wi-Fi is now the hotspot, it needs Ethernet or a USB Wi-Fi adapter for internet (that's the thing from step 1). To undo it: `sudo nmcli connection delete Hotspot`.

If you'd rather leave the Pi on your home Wi-Fi, skip this step and use `http://<pi-ip>:8080`.

Rough render times for one 9-second reel with software encoding, and I haven't tried any of this on a real Pi yet, so treat these as guesses: Pi 5 about 15 to 30 seconds, Pi 4 about 30 to 90 seconds, Pi 3 or Zero 2 W a few minutes. Slow renders don't really matter for scheduled posts. Previews just take longer.

#### 6. Change the panel password

The panel login is `admin` / `larp` out of the box, and anyone on the hotspot can reach it. Set a new password in the Accounts tab before you do anything else.

---

## First run

Open the panel (`http://127.0.0.1:8080` on the same computer, or the Pi's address) and log in with `admin` / `larp`.

1. Accounts tab: set a new panel password. Paste in a free Pexels and/or Pixabay key if you want stock footage. Add your Instagram username and password, plus the authenticator secret if the account uses 2FA. Hit Save settings.
2. Content tab: pick where footage comes from. The default, Auto, uses whatever you've set up, and with no keys it falls back to the TikTok creator source, which needs nothing. Tweak the themes, hashtags and caption if you feel like it.
3. Music tab (optional): paste YouTube links, one per line, each with an optional start time. "Start songs at their best part" is ticked by default. Read the rights note on that tab.
4. Press Preview in the top bar. It saves your settings, makes a reel and shows it on the Home tab without posting anything. The first run is slower because it's downloading footage and music. Keep hitting it until you like what comes out.
5. Schedule tab: pick your days (certain weekdays, or every few days), set post times (24-hour clock) and your timezone, then tick "Post automatically". Leave "Dry run" ticked for the first day, so it makes reels on schedule but never touches Instagram.
6. Once you're happy, untick "Dry run" and Save settings. Posts now go out at the scheduled times. "Post now" in the top bar posts right away. While Dry run is on, that same button says "Test post" and posts nothing.

The Home tab has a setup checklist that shows what's ready and what still needs a look.

LarpCron has to be running for the schedule to fire, so use systemd on a Pi or Linux box, or Task Scheduler on Windows.

You can also render one reel without the panel: `python -m larpcron --once` (it lands in `data/out/`).

Command line options look like `python -m larpcron --host 127.0.0.1 --port 9000`. Use `--host 127.0.0.1` to only allow the computer it's running on. The default `0.0.0.0` lets other devices on the network in, which is what the Pi needs.

## Posting on a schedule

You don't have to post every day. The Schedule tab has:

- Which days: tick certain weekdays (there are quick picks for weekdays, weekends and Mon/Wed/Fri), or choose "every few days" with a start date, like every 3 days.
- Times: one or more a day, used on each posting day.
- Make it less regular: shift each post by up to some number of minutes, and set a percent chance that each slot goes out, so some get skipped at random.
- Pause until: pick a date and nothing posts through it, for when you're away.
- Coming up: the next posting slots under your saved settings.

The scheduler fires at every configured time each day and checks the day rules right then, so daylight saving clock changes don't mess up the pattern. Random shifts and skipped posts get decided at that moment too, which means Coming up shows the planned slots, not what will definitely happen.

## Updating

```bash
cd LarpCron
git pull
source .venv/bin/activate              # Windows: .venv\Scripts\Activate.ps1
pip install -r requirements.txt
sudo systemctl restart larpcron        # if running as a service; otherwise stop and rerun it
```

Your settings, keys, cache and history all live in `data/`, which git ignores, so updating won't touch any of it. If TikTok or YouTube downloads suddenly stop working, `pip install -U yt-dlp` usually fixes it.

Moving to a new machine? Copy over `data/config.json`. It holds your settings and credentials.

## When stuff breaks

| Problem | Fix |
|---|---|
| "No footage available for source ..." | The footage source has nothing to pull from. Switch it to Auto or A TikTok creator, add a Pexels/Pixabay key (Accounts tab), or drop clips in `assets/backgrounds/`. The Home checklist shows which sources are ready. |
| Panel won't load from my phone | Make sure it's running with the default `--host 0.0.0.0` (not `127.0.0.1`), the phone is on the same network or the Pi's hotspot, and nothing is blocking port 8080. |
| Page says login required / forgot the password | Edit `data/config.json`, set `"ui_password"`, and restart. The username is always `admin`. |
| `ffmpeg` not found / crashes on a 32-bit Pi | `sudo apt install ffmpeg`, or reinstall with 64-bit Raspberry Pi OS. |
| Quote text looks tiny or the font is off on Linux | `sudo apt install fonts-dejavu-core`. |
| Music link fails or the reel is silent | The log (bottom of the Home tab) says why. Update yt-dlp (`pip install -U yt-dlp`), and on a Pi run `sudo apt install nodejs`. The video might also be unavailable or region-locked. LarpCron then falls back to local tracks, or a silent reel. |
| TikTok clip download fails | `pip install -U yt-dlp`. TikTok may also be blocking your IP, so try again later. |
| Instagram login fails / "challenge required" | Open the Instagram app on your phone and approve the "was this you?" prompt, then try again. Delete `data/instagram_session.json` to force a fresh login. New IPs logging in a lot is what usually triggers these. |
| Reel posted but muted | Instagram caught copyrighted music. Use royalty-free tracks. |
| Schedule didn't fire | "Post automatically" has to be ticked and saved, LarpCron had to be running at that time, and the timezone has to be right (Schedule tab). A run that's up to 10 minutes late still fires. |
| `pip install` fails on a Pi | Update first (`sudo apt full-upgrade`), make sure you're on 64-bit OS, and retry. Big wheels can time out on a slow connection. |
| Port 8080 already in use | Run it on another port: `python -m larpcron --port 9000`. |

---

## Footage

By default LarpCron uses every footage source you've set up and grabs a fresh clip each time:

- Pexels: free key at <https://www.pexels.com/api/> (portrait clips, which suit reels best)
- Pixabay: free key at <https://pixabay.com/api/docs/> (bigger pool, but mostly landscape, so it gets center-cropped to 9:16)
- TikTok creator: no key needed (more below)
- Your own clips in `assets/backgrounds/`

The stock sources search using your Themes list. With both keys it picks between them at random and falls back to the other if one fails. Both licenses allow free use without attribution, but read them for your own situation. You can set the footage source to `any` (default), `online` (Pexels + Pixabay), `pexels`, `pixabay`, `tiktok` or `local` (only `assets/backgrounds/`).

For the TikTok creator source, set footage source to `tiktok` (or leave it on `any`). LarpCron picks a random video from the profile in "TikTok creator profile" (default `https://www.tiktok.com/@waywroxc`), downloads it with [yt-dlp](https://github.com/yt-dlp/yt-dlp), and strips the audio. It's only okay for clips you're allowed to reuse, and LarpCron can't check that, so look at the creator's terms yourself. Downloading also goes against TikTok's terms of service. If it breaks after a TikTok change, run `pip install -U yt-dlp`.

Got your own clips? Put them in `assets/backgrounds/`. If you have a long compilation you've got the rights to, cut it into clips with `python -m larpcron split my_compilation.mp4` (use `--threshold 0.25` for more cuts, `--max 12` to cap clip length). LarpCron doesn't download videos from YouTube as footage.

## Music

Drop audio files (`.mp3 .m4a .wav .aac .ogg .flac`) in `assets/music/` and one gets picked at random for each reel. No music means a silent reel. To start each song at a specific moment, add `assets/music/playlist.json`:

```json
[
  {"file": "track1.mp3", "start": "1:12"},
  {"file": "track2.mp3", "start": "0:45", "volume": 0.7}
]
```

`start` takes `72`, `1:12` or `1:02:03`, and `volume` is optional (1.0 means unchanged). Entries whose file is missing get skipped.

You can also paste YouTube links into the panel's Music box, one per line, with an optional start time after the link (`https://www.youtube.com/watch?v=XXXXXXXXXXX 1:12`). The audio gets downloaded once with yt-dlp, cached in `data/music_cache/`, and joins the random pick. If a link fails, LarpCron falls back to your local tracks, or a silent reel. Spotify links don't work, since Spotify doesn't give out audio. yt-dlp might warn about a missing JavaScript runtime. Installing Node.js or [deno](https://deno.com) silences that and helps with some videos.

Smart music start is the "Start songs at their best part" box on the Music tab. It's on by default, and the config key is `smart_music_start`. It starts each song at its best part instead of the intro, in this order:

1. A start time you set yourself, after a YouTube link or in `playlist.json`, always wins.
2. For YouTube links, it uses the best window (as long as the reel) on YouTube's "Most replayed" graph, if the video has one.
3. Otherwise it finds the loudest stretch of the audio with ffmpeg and numpy. That works for any file in `assets/music/` too.

Results are cached in `data/music_cache/smart_starts.json`, so each track only gets analyzed once.

> You're on the hook for having the rights to every track you add. Downloading from YouTube may break YouTube's terms of service, and commercial songs in an uploaded video tend to get muted or flagged by Instagram, which can mean strikes on your account. Royalty-free music is the safe move (Pixabay Music, YouTube Audio Library, Mixkit, Jamendo).

## Every setting

Everything is editable in the panel and saved in `data/config.json`, which is gitignored so your credentials never end up in a commit. You can edit the file by hand too, as long as LarpCron is stopped.

| Key | Default | What it does |
|---|---|---|
| `instagram_username` / `instagram_password` | empty | Instagram login |
| `instagram_2fa_seed` | empty | Authenticator-app secret, if the account uses 2FA |
| `footage_source` | `any` | `any`, `online`, `pexels`, `pixabay`, `tiktok`, `local` |
| `tiktok_profile` | `https://www.tiktok.com/@waywroxc` | Creator profile for the TikTok source |
| `pexels_api_key` / `pixabay_api_key` | empty | Free stock-footage keys |
| `themes` | luxury car, miami skyline, ... | Search terms for the stock sources |
| `content_mode` | `reel` | `reel`, `photo` or `mix` |
| `reel_seconds` | `9` | Reel length (5 to 60) |
| `quote_source` | `mixed` | `local`, `online` or `mixed` |
| `hashtags`, `caption_template`, `watermark` | see panel | The caption (`{quote}`, `{author}`, `{hashtags}`) and optional on-video text |
| `music_links` | empty | YouTube links with optional start times |
| `smart_music_start` | `true` | Start music at its best part (your own start times still win) |
| `post_times`, `timezone` | `12:00, 19:00`, `America/New_York` | When to post (24-hour clock) |
| `schedule_mode` | `weekdays` | `weekdays` (uses `post_days`) or `interval` (every `every_n_days` days) |
| `post_days` | all seven | Days to post on in weekdays mode, Monday = 0 to Sunday = 6 |
| `every_n_days`, `interval_anchor` | `2`, empty | Interval mode: post every N days, counted from this date (empty means the day you saved) |
| `time_jitter_min` | `0` | Shift each post earlier or later by up to this many minutes, at random |
| `post_chance` | `100` | Percent chance that each scheduled slot actually posts |
| `pause_until` | empty | A date (YYYY-MM-DD). Nothing posts through that day |
| `enabled` | `false` | Post automatically on the schedule |
| `dry_run` | `true` | Make reels but never post them |
| `ui_password` | `larp` | Panel password (username is `admin`) |

## Before you post for real

- LarpCron posts through Instagram's unofficial private API, using [instagrapi](https://github.com/subzeroid/instagrapi). The official Graph API wants a Business account, a Facebook Page and a public URL for every video, which doesn't work well on a Pi. The catch is that automated posting breaks Instagram's Terms of Use and can get you login challenges or a ban. Use an account you can afford to lose, don't post more than a couple of times a day, and leave Dry run on until you like the results.
- Pexels and Pixabay footage is free to use, but check their licenses for your situation. A lot of quotes are public domain, and the caption credits the author by default.
- The panel uses basic auth over plain HTTP. That's fine on your own hotspot or home network, but don't put it on the open internet.
- Credentials sit in plain text in `data/config.json`, so keep that file off shared machines.

## What's where

```
larpcron/
  config.py     settings file + defaults
  media.py      footage: Pexels, Pixabay, TikTok creator, local clips
  quotes.py     quote sources
  music.py      YouTube audio download + smart start (heatmap / loudest window)
  reel.py       Pillow overlay + ffmpeg render (reel or 4:5 photo), music selection
  split.py      cut a compilation into clips
  instagram.py  uploader
  job.py        render + post pipeline, history, logs
  scheduler.py  APScheduler cron jobs
  web.py        control panel (templates/index.html)
assets/         your own clips (backgrounds/) and music
deploy/         systemd unit, hotspot script
```

## Credits

The panel's background photo (`larpcron/static/city.jpg`) is "Manhattan in the distance" by Luca Bravo, released to the public domain under CC0 ([source on Wikimedia Commons](https://commons.wikimedia.org/wiki/File:Manhattan_in_the_distance_(Unsplash).jpg)). I resized it and darkened it a little. Want your own? Swap that file for any JPEG. Around 2400 px wide and under 600 KB works well.

MIT licensed.
