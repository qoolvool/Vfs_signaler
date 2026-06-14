# VFS Signaler — VFS Global Croatia (Belgrade) appointment watcher

The bot logs into the [VFS Global Croatia](https://visa.vfsglobal.com/srb/en/hrv/login)
account, completes the OTP confirmation sent by email, and periodically checks
the appointment page for available slots for the chosen application centre /
category / sub-category. When a slot appears, the bot sends a Telegram
notification, and when the slot disappears (was available and is gone again)
it sends a separate notification about that too.

The bot **does not book a slot automatically** — it only notifies you so you
can go and book it manually in time.

## Installation

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
patchright install chromium
```

The bot uses **[patchright](https://github.com/Kaliiiiiiiiii-Vinyzu/patchright)**
(a Playwright fork that hides automation signals at the CDP level) and launches
**real Google Chrome** (via `channel="chrome"`) instead of the bundled
Chromium — this significantly reduces the chance of a Cloudflare block. So
Google Chrome must be installed on the machine:
- Download and install it from [google.com/chrome](https://www.google.com/chrome/).

If Chrome is not found, the bot automatically falls back to the bundled
Chromium (but the chances of passing Cloudflare are lower).

## Configuration

1. Copy `.env.example` to `.env` and fill in:
   - `VFS_EMAIL` / `VFS_PASSWORD` — your VFS Global account credentials.
   - `IMAP_USERNAME` / `IMAP_PASSWORD` — the mailbox that receives the OTP code
     (for Gmail, use an [app password](https://support.google.com/accounts/answer/185833)).
   - `TELEGRAM_BOT_TOKEN` / `TELEGRAM_CHAT_ID` — for notifications (optional;
     if not set, notifications go to the log only).
   - `PROXY_SERVER` / `PROXY_USERNAME` / `PROXY_PASSWORD` — proxy
     (optional, but **strongly recommended**, see below).

2. Copy `config.example.yaml` to `config.yaml` and adjust as needed:
   - `application_centre`, `category`, `sub_category` — the exact values
     shown in the dropdowns on the appointment page (e.g.
     `"Visa Application Centre, Belgrade"`, `"C visa"`,
     `"Tourist , Visit , Business"`).
   - `poll_interval_min_seconds` / `poll_interval_max_seconds` — bounds of the
     random interval between checks (default 120–300 sec, i.e. 2–5 minutes).
     A random interval within this range is picked on every cycle so requests
     aren't perfectly regular and don't look like scraping to Cloudflare.
   - `reminder_interval_seconds` — if > 0, the bot will repeat the Telegram
     notification every N seconds while the slot is still available (useful if
     you didn't notice the first message right away). Default `0` — notify
     only when the slot first appears.
   - `access_denied_backoff_seconds` — how long to pause after VFS hard-blocks
     with "Access Denied / 429002 Unauthorised Activity" (a rate-limit block).
     Retrying quickly only extends the block, so the default is 30 minutes.
   - `headless: false` — it's recommended to keep the browser visible,
     especially on the first run, until you've confirmed Cloudflare/OTP pass
     successfully.

## Running

```bash
python -m vfs_bot.main
```

On the first run, the bot will:
1. Open the login page, enter the email/password, and wait for Cloudflare to
   pass.
2. On the OTP page, wait for VFS's email in your mailbox, grab the code, and
   enter it.
3. After a successful login, save cookies to `storage_state.json` — on
   subsequent runs, no repeated login/OTP is needed while the session is
   alive. Cookies are also saved after every check cycle (atomic write), so
   the session isn't lost if the process crashes.
4. Go to the appointment page, select the centre/category/sub-category, and
   check for available slots at the configured interval.

## Debugging: seeing what the bot is doing

There are three ways to understand what's happening:

1. **Console logs.** The bot logs every step of the login flow in detail:
   ```
   Login step: opening login page ...
   Login step: entering email
   Login step: entering password
   Login step: waiting for Cloudflare on login page
   Login step: clicking Sign In (credentials)
   Login step: waiting for OTP input field
   Login step: waiting for OTP email
   Login step: OTP received, entering it
   Login step: waiting for Cloudflare on OTP page
   Login step: clicking Sign In (OTP)
   Login step: waiting for redirect to appointment page
   Login successful
   ```
   If the bot hangs or crashes, the last line immediately shows which step it
   was on.

2. **Step-by-step screenshots** (`vfs.debug_screenshots: true`, enabled by
   default). At every key login step (login page, after filling in
   credentials, after Cloudflare, OTP page, after entering OTP,
   success/failure) a PNG is saved into `vfs.debug_dir` (default `debug/`).
   This is useful even without a GUI — you can just download the folder and
   look at the images.

3. **Telegram notifications with screenshots.**
   - After a successful login, Telegram receives a screenshot of the
     appointment page — visual confirmation that the bot actually got into the
     account.
   - If login fails, you get a screenshot of whatever it got stuck on
     (login/OTP page, Cloudflare error).
   - Any error during a check cycle also comes with a screenshot.

Besides screenshots, with `headless: false` and access to a graphical
environment (e.g. X11/VNC on a server, or running on your own machine) you can
simply watch the open browser window in real time.

## Human-like behaviour

During login, the bot doesn't paste the email/password/OTP instantly —
instead it types them character by character with randomized delays
(`vfs_bot/human.py`), clears each field before typing like a real user, moves
the mouse towards buttons before clicking, and adds random pauses between
steps. This reduces the chance of Cloudflare Turnstile flagging the session as
automated based on behavioural signals. Because of this, login takes 10-20
seconds longer — that's expected.

## Proxy (important for getting past Cloudflare)

Cloudflare almost always blocks access to VFS Global from datacenter IPs (VPS,
cloud, hosting). If the bot sees a page saying something like *"try again in
one hour"* or fails the challenge, it's almost certainly an IP issue. The
solution is a **residential or mobile proxy**.

Configuration: set `proxy.server` in `config.yaml` or `PROXY_SERVER` in
`.env`:

```yaml
proxy:
  server: "http://1.2.3.4:8080"      # or socks5://1.2.3.4:1080
```

Proxy credentials go in `.env` (`PROXY_USERNAME` / `PROXY_PASSWORD`) so they
aren't stored in the repository. If `server` is empty, the bot runs without a
proxy.

Recommendations:
- Use a **residential** proxy, not a datacenter one — Cloudflare blocks the
  latter too, often aggressively.
- A **sticky** IP that doesn't change between login and polling is preferable,
  otherwise the session may get invalidated.
- Pick an IP region close to Serbia/the Balkans if possible.

## A note on selectors

VFS Global periodically changes its site layout and strengthens Cloudflare
protection. The selectors in `vfs_bot/client.py` are written to be as robust
as possible (by label text and button roles), but if the site changes,
`client.py` will need updating. It helps to run with `headless: false` and/or
use `playwright codegen https://visa.vfsglobal.com/srb/en/hrv/login` to inspect
the current markup.

## Security

- Don't commit `.env`, `config.yaml`, or `storage_state.json` — they're
  already in `.gitignore`.
- Only use this bot to check slot availability for **your own** VFS Global
  account.
