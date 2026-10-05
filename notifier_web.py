#!/usr/bin/env python3
"""
Notifier Web v3 -- message scheduler + admin interface on Render's FREE plan.

What's new in v3:
  * A web interface at /ui: add, edit, delete and test-fire
    messages from your phone or laptop -- no code edits needed.
    (The root URL / returns a tiny status line instead, so keep-alive
    pingers like cron-job.org -- which reject responses over ~1 kB --
    never see a "response too big" error.)
  * The scheduler auto-reloads messages.json when it changes.
  * OPTIONAL GitHub persistence: Render free instances have ephemeral
    storage, so UI edits would be lost on every restart/redeploy. If you
    set GITHUB_REPO + GITHUB_TOKEN, every change is also committed to
    your repo (and Render auto-redeploys from it).

Environment variables
---------------------
  NTFY_TOPIC          ntfy topic to push to (required)
  START_MESSAGE       startup push text (default "Notifier started - ...")
  SEND_STARTUP_MESSAGE  set to 0 to disable the startup push
  RUN_SCHEDULER       set to 0 for editor mode -- the interface only
                      manages messages.json (with GitHub persistence);
                      use this when a Render cron job does the sending
  ADMIN_PASSWORD      if set, the interface asks for this password
  GITHUB_REPO         "user/repo" -- enable GitHub persistence
  GITHUB_TOKEN        a fine-grained PAT with Contents: Read/Write on it
  GITHUB_BRANCH       default "main"
  GITHUB_PATH         default "messages.json"

Deploying is the same as before -- see README.md. Locally you can run
this file directly and open http://localhost:10000 to use the interface.

Requires only Python 3.9+ (standard library only).
"""

import base64
import datetime
import html
import hmac
import json
import os
import pathlib
import re
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from zoneinfo import ZoneInfo

# ------------------------------------------------------------------
# Config
# ------------------------------------------------------------------
IST = ZoneInfo("Asia/Kolkata")
NTFY_SERVER = os.environ.get("NTFY_SERVER", "https://ntfy.sh")
NTFY_TOPIC = os.environ.get("NTFY_TOPIC", "my-alerts-x7k2pq")  # <-- CHANGE
NTFY_TOKEN = os.environ.get("NTFY_TOKEN") or None  # optional access token
MESSAGES_FILE = pathlib.Path(__file__).resolve().parent / "messages.json"
PORT = int(os.environ.get("PORT", "10000"))

STARTUP_MESSAGE = os.environ.get(
    "START_MESSAGE", "Notifier started - your schedule is live.")
SEND_STARTUP_MESSAGE = os.environ.get("SEND_STARTUP_MESSAGE", "1") != "0"
RUN_SCHEDULER = os.environ.get("RUN_SCHEDULER", "1") != "0"
ADMIN_PASSWORD = os.environ.get("ADMIN_PASSWORD") or None

GITHUB_REPO = os.environ.get("GITHUB_REPO") or None          # "user/repo"
GITHUB_TOKEN = os.environ.get("GITHUB_TOKEN") or None
GITHUB_BRANCH = os.environ.get("GITHUB_BRANCH", "main")
GITHUB_PATH = os.environ.get("GITHUB_PATH", "messages.json")

DAY_ALIASES = {
    "mon": "monday", "tue": "tuesday", "wed": "wednesday",
    "thu": "thursday", "fri": "friday", "sat": "saturday", "sun": "sunday",
}
DAY_ORDER = ["monday", "tuesday", "wednesday", "thursday", "friday",
             "saturday", "sunday"]
PRIORITIES = ["min", "low", "default", "high", "urgent"]
SHORT_DAY = {"monday": "mon", "tuesday": "tue", "wednesday": "wed",
             "thursday": "thu", "friday": "fri", "saturday": "sat",
             "sunday": "sun"}

SAVE_LOCK = threading.Lock()      # serialize edits + reloads


# ------------------------------------------------------------------
# Sending
# ------------------------------------------------------------------
def send_notification(text, title="Reminder", priority="default",
                      tags="bell"):
    """POST a message to the ntfy topic. Returns (ok, detail).

    ok is True when ntfy accepted the message. detail explains what the
    ntfy server actually said, so the UI and logs show the real reason
    for a failure instead of a generic message.
    """
    headers = {"Title": title, "Priority": priority, "Tags": tags}
    if NTFY_TOKEN:
        # ntfy.sh per-account quota or self-hosted server
        headers["Authorization"] = f"Bearer {NTFY_TOKEN}"
    request = urllib.request.Request(
        f"{NTFY_SERVER}/{NTFY_TOPIC}",
        data=text.encode("utf-8"),
        method="POST",
        headers=headers,
    )
    try:
        with urllib.request.urlopen(request, timeout=10) as response:
            ok = 200 <= response.status < 300
            print(f"[sent] {title}: {text} (HTTP {response.status})")
            return ok, f"HTTP {response.status}"
    except urllib.error.HTTPError as exc:
        detail = f"HTTP {exc.code} from {NTFY_SERVER}"
        try:
            body = json.loads(exc.read().decode("utf-8", "replace"))
            if body.get("error"):
                detail += f" - {body['error']}"
        except Exception:  # noqa: BLE001 - body may not be JSON
            pass
        print(f"[ERROR] Could not send '{title}': {detail}")
        return False, detail
    except Exception as exc:  # noqa: BLE001 - report any network error
        detail = f"{type(exc).__name__}: {exc}"
        print(f"[ERROR] Could not send '{title}': {detail}")
        return False, detail


def topic_warning():
    """Return a warning string if NTFY_TOPIC looks wrong, else None."""
    if not re.fullmatch(r"[-_A-Za-z0-9]{1,64}", NTFY_TOPIC):
        return (f"NTFY_TOPIC is '{NTFY_TOPIC}' -- topic names may only "
                "contain letters, numbers, '-' and '_' (no spaces or "
                "symbols), so ntfy will reject it.")
    if NTFY_TOPIC == "my-alerts-x7k2pq":
        return ("NTFY_TOPIC is not set -- using the fallback topic from "
                "the code. Set the NTFY_TOPIC environment variable on "
                "Render to the topic your phone is subscribed to.")
    return None


HINDI_QUOTES_URL = "https://hindi-quotes.vercel.app/random"
QUOTE_CATEGORIES = ("positive", "success", "love", "attitude",
                    "motivational")


def fetch_quote(category=None):
    """Random Hindi quote from hindi-quotes.vercel.app (free, no key).

    'category' optionally restricts the quote to one of
    QUOTE_CATEGORIES. Returns the quote text, or None on any failure.
    """
    url = HINDI_QUOTES_URL
    if category and str(category).lower() in QUOTE_CATEGORIES:
        url += "/" + str(category).lower()
    try:
        request = urllib.request.Request(
            url, headers={"User-Agent": "phone-notifier"})
        with urllib.request.urlopen(request, timeout=10) as response:
            data = json.loads(response.read().decode("utf-8"))
        if isinstance(data, list):      # tolerate either response shape
            data = data[0]
        quote = str(data.get("quote", "")).strip()
        return quote or None
    except Exception as exc:  # noqa: BLE001 - the quote is best-effort
        print(f"[quote] could not fetch Hindi quote: {exc}")
        return None


def message_text(msg):
    """Message text, with the daily Hindi quote appended if requested."""
    text = msg["text"]
    if msg.get("quote"):
        setting = msg["quote"]
        category = setting if isinstance(setting, str) else None
        quote = fetch_quote(category)
        if quote:
            text = f"{text}\n\n{quote}"
    return text


# ------------------------------------------------------------------
# GitHub persistence (optional)
# ------------------------------------------------------------------
def _github_headers():
    return {
        "Authorization": f"Bearer {GITHUB_TOKEN}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    }


def github_pull():
    """Fetch messages.json from GitHub. Returns (content_bytes, sha) or None."""
    if not (GITHUB_REPO and GITHUB_TOKEN):
        return None
    url = (f"https://api.github.com/repos/{GITHUB_REPO}/contents/"
           f"{urllib.parse.quote(GITHUB_PATH)}?ref={GITHUB_BRANCH}")
    try:
        req = urllib.request.Request(url, headers=_github_headers())
        with urllib.request.urlopen(req, timeout=15) as response:
            data = json.loads(response.read())
        return base64.b64decode(data["content"]), data["sha"]
    except Exception as exc:  # noqa: BLE001
        print(f"[github] pull failed: {exc}")
        return None


def github_push(content_bytes):
    """Commit messages.json to GitHub. Returns error string or None."""
    if not (GITHUB_REPO and GITHUB_TOKEN):
        return None
    # find the current sha (file may or may not exist yet)
    sha = None
    url = (f"https://api.github.com/repos/{GITHUB_REPO}/contents/"
           f"{urllib.parse.quote(GITHUB_PATH)}")
    try:
        req = urllib.request.Request(
            f"{url}?ref={GITHUB_BRANCH}", headers=_github_headers())
        with urllib.request.urlopen(req, timeout=15) as response:
            sha = json.loads(response.read())["sha"]
    except urllib.error.HTTPError as exc:
        if exc.code != 404:
            return f"github read failed: HTTP {exc.code}"
    except Exception as exc:  # noqa: BLE001
        return f"github read failed: {exc}"

    body = json.dumps({
        "message": "Update schedule from notifier UI",
        "content": base64.b64encode(content_bytes).decode("ascii"),
        "branch": GITHUB_BRANCH,
        **({"sha": sha} if sha else {}),
    }).encode("utf-8")
    try:
        req = urllib.request.Request(url, data=body, method="PUT",
                                      headers=_github_headers())
        with urllib.request.urlopen(req, timeout=15) as response:
            json.loads(response.read())
        print("[github] committed schedule change")
        return None
    except Exception as exc:  # noqa: BLE001
        return f"github commit failed: {exc}"


def boot_load():
    """At startup: pull from GitHub if configured, else use local file."""
    pulled = github_pull()
    if pulled and pulled[0].strip():
        content, _ = pulled
        json.loads(content)               # validate before writing
        MESSAGES_FILE.write_bytes(content)
        print(f"[github] loaded schedule from {GITHUB_REPO}/{GITHUB_PATH}")
    elif GITHUB_REPO:
        print("[github] could not pull; using local messages.json")


# ------------------------------------------------------------------
# Schedule storage
# ------------------------------------------------------------------
def load_messages():
    with open(MESSAGES_FILE, "r", encoding="utf-8") as fh:
        messages = json.load(fh)
    for index, msg in enumerate(messages):
        for field in ("time", "text"):
            if field not in msg:
                raise ValueError(
                    f"Message #{index + 1} is missing the '{field}' field.")
        msg.setdefault("title", "Reminder")
        msg.setdefault("priority", "default")
        msg.setdefault("days", "everyday")
        if "date" in msg:
            d = str(msg["date"])
            if re.fullmatch(r"\d{2}-\d{2}", d):            # MM-DD, yearly
                year, mm, dd = "2024", d[:2], d[3:]           # 2024: leap year
            elif re.fullmatch(r"\d{4}-\d{2}-\d{2}", d):     # one-time
                year, mm, dd = d[:4], d[5:7], d[8:]
            else:
                raise ValueError(
                    f"Message #{index + 1} has a bad 'date' field "
                    f"(expected MM-DD or YYYY-MM-DD): {msg['date']!r}.")
            try:
                datetime.date(int(year), int(mm), int(dd))
            except ValueError:
                raise ValueError(
                    f"Message #{index + 1} has a 'date' that is not a real "
                    f"calendar date: {msg['date']!r}.")
    return messages


def save_messages(messages):
    """Write locally and commit to GitHub if configured.
    Returns an error string (GitHub problems only) or None."""
    with SAVE_LOCK:
        content = (json.dumps(messages, indent=2, ensure_ascii=False)
                   + "\n").encode("utf-8")
        MESSAGES_FILE.write_bytes(content)
        return github_push(content)


def day_matches(days_setting, today_name):
    weekdays = {"monday", "tuesday", "wednesday", "thursday", "friday"}
    if days_setting == "everyday":
        return True
    if days_setting == "weekdays":
        return today_name in weekdays
    if days_setting == "weekends":
        return today_name not in weekdays
    wanted = {DAY_ALIASES.get(str(d).lower(), str(d).lower())
              for d in days_setting}
    return today_name in wanted


def describe_days(days_setting):
    if isinstance(days_setting, str):
        return {"everyday": "Every day", "weekdays": "Weekdays",
                "weekends": "Weekends"}.get(days_setting, days_setting)
    return ", ".join(SHORT_DAY.get(d, d) for d in days_setting)


MONTH_NAMES = ["Jan", "Feb", "Mar", "Apr", "May", "Jun",
               "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]


def describe_when(msg):
    """Human label for when a message fires.

    'Yearly on 14 Mar' / 'Once on 14 Mar 2027' / weekday description."""
    date_value = msg.get("date")
    if date_value:
        d = str(date_value)
        if len(d) == 10:                      # YYYY-MM-DD -> one-time
            yyyy, mm, dd = d.split("-")
            return (f"Once on {int(dd)} {MONTH_NAMES[int(mm) - 1]} {yyyy}")
        mm, dd = d.split("-")
        return f"Yearly on {int(dd)} {MONTH_NAMES[int(mm) - 1]}"
    return describe_days(msg["days"])


# ------------------------------------------------------------------
# Scheduler (auto-reloads messages.json when it changes)
# ------------------------------------------------------------------
def run_scheduler():
    messages = None
    while messages is None:          # keep retrying if messages.json is bad
        try:
            messages = load_messages()
        except Exception as exc:  # noqa: BLE001 - report and retry
            print(f"[scheduler] could not load {MESSAGES_FILE.name}: {exc}"
                  f" -- retrying in 60 seconds")
            time.sleep(60)

    print(f"[scheduler] loaded {len(messages)} message(s), "
          f"topic '{NTFY_TOPIC}', timezone Asia/Kolkata")

    fired = set()
    current_date = None
    last_mtime = MESSAGES_FILE.stat().st_mtime if MESSAGES_FILE.exists() else 0

    while True:
        now = datetime.datetime.now(IST)

        # pick up UI edits without restarting
        try:
            mtime = MESSAGES_FILE.stat().st_mtime
        except OSError:
            mtime = 0
        if mtime != last_mtime:
            last_mtime = mtime
            try:
                with SAVE_LOCK:
                    messages = load_messages()
                print(f"[scheduler] reloaded schedule "
                      f"({len(messages)} message(s))")
            except Exception as exc:  # noqa: BLE001
                print(f"[scheduler] reload failed, keeping old: {exc}")

        hhmm = now.strftime("%H:%M")
        today_name = now.strftime("%A").lower()

        if now.date() != current_date:      # new day -> reset memory
            current_date = now.date()
            fired.clear()
            print(f"[scheduler] --- {current_date} ---")

        for index, msg in enumerate(messages):
            key = (index, msg["time"])
            if "date" in msg:   # dated: MM-DD yearly, YYYY-MM-DD once
                d = str(msg["date"])
                day_ok = (now.strftime("%Y-%m-%d") == d if len(d) == 10
                          else now.strftime("%m-%d") == d)
            else:
                day_ok = day_matches(msg["days"], today_name)
            if msg["time"] == hhmm and key not in fired and day_ok:
                send_notification(message_text(msg), msg["title"],
                                  msg["priority"])
                fired.add(key)

        time.sleep(20)


# ------------------------------------------------------------------
# Web interface
# ------------------------------------------------------------------
STYLE = """
:root{
  --bg:#f6f7f9; --card:#ffffff; --text:#1f2933; --muted:#5b6675;
  --line:#e3e6ea; --accent:#2563eb; --accent-ink:#ffffff;
  --danger:#dc2626;
  --warn-bg:#fff7ed; --warn-line:#fed7aa; --warn-ink:#9a3412;
  --ok-bg:#f0fdf4; --ok-line:#bbf7d0; --ok-ink:#166534;
  --err-bg:#fef2f2; --err-line:#fecaca; --err-ink:#991b1b;
  --shadow:0 1px 2px rgba(16,24,40,.06), 0 1px 3px rgba(16,24,40,.08);
  --radius:14px;
}
@media (prefers-color-scheme: dark){
  :root:not([data-theme=light]){
    --bg:#12161c; --card:#1a2029; --text:#e7ebf0; --muted:#98a5b4;
    --line:#2a323d; --accent:#5b8def; --accent-ink:#0b1220;
    --danger:#f87171;
    --warn-bg:#2a2016; --warn-line:#5b3a1a; --warn-ink:#fbbf24;
    --ok-bg:#12251a; --ok-line:#1f4d33; --ok-ink:#86efac;
    --err-bg:#2a1618; --err-line:#5b2529; --err-ink:#fca5a5;
    --shadow:0 1px 2px rgba(0,0,0,.35);
  }
}
:root[data-theme=dark]{
  --bg:#12161c; --card:#1a2029; --text:#e7ebf0; --muted:#98a5b4;
  --line:#2a323d; --accent:#5b8def; --accent-ink:#0b1220;
  --danger:#f87171;
  --warn-bg:#2a2016; --warn-line:#5b3a1a; --warn-ink:#fbbf24;
  --ok-bg:#12251a; --ok-line:#1f4d33; --ok-ink:#86efac;
  --err-bg:#2a1618; --err-line:#5b2529; --err-ink:#fca5a5;
  --shadow:0 1px 2px rgba(0,0,0,.35);
}
*{box-sizing:border-box}
body{margin:0;padding:20px 16px 56px;background:var(--bg);color:var(--text);
  font-family:system-ui,-apple-system,"Segoe UI",Roboto,"Helvetica Neue",sans-serif;
  font-size:16px;line-height:1.55;-webkit-font-smoothing:antialiased;}
.wrap{max-width:680px;margin:0 auto;}
header.top{display:flex;align-items:center;gap:12px;justify-content:space-between;}
h1{font-size:clamp(1.3rem,5vw,1.6rem);margin:0;letter-spacing:-.01em;}
h2{font-size:1.05rem;margin:0 0 10px;letter-spacing:-.005em;}
.sub{color:var(--muted);font-size:.83rem;margin:6px 0 16px;}
.note{color:var(--muted);font-size:.88rem;margin:6px 0;}
.theme-toggle{background:transparent;border:1px solid var(--line);color:var(--muted);
  border-radius:999px;width:38px;height:38px;font-size:1rem;line-height:1;
  cursor:pointer;padding:0;margin:0;flex:none;}
.theme-toggle:hover{color:var(--text);border-color:var(--muted);}
.alert{padding:11px 14px;border-radius:10px;margin:0 0 14px;font-size:.87rem;
  border:1px solid var(--err-line);background:var(--err-bg);color:var(--err-ink);}
.alert.ok{border-color:var(--ok-line);background:var(--ok-bg);color:var(--ok-ink);}
ul.schedule{list-style:none;margin:0;padding:0;display:flex;
  flex-direction:column;gap:10px;}
li.msg{background:var(--card);border:1px solid var(--line);
  border-radius:var(--radius);padding:14px;box-shadow:var(--shadow);
  display:flex;gap:14px;flex-wrap:wrap;}
.time{font-variant-numeric:tabular-nums;font-weight:650;font-size:1.05rem;
  min-width:3.7rem;letter-spacing:-.01em;}
.msg-body{flex:1 1 12rem;min-width:0;}
.msg-title{font-weight:600;}
.msg-text{color:var(--muted);font-size:.88rem;margin-top:2px;
  overflow-wrap:anywhere;}
.chips{display:flex;flex-wrap:wrap;gap:6px;margin-top:9px;}
.chip{font-size:.71rem;color:var(--muted);background:var(--bg);
  border:1px solid var(--line);padding:2px 9px;border-radius:999px;
  white-space:nowrap;}
.chip.prio-high{color:var(--warn-ink);border-color:var(--warn-line);
  background:var(--warn-bg);}
.chip.prio-urgent{color:var(--err-ink);border-color:var(--err-line);
  background:var(--err-bg);font-weight:600;}
.chip.quote{color:var(--accent);}
.actions{display:flex;gap:8px;flex-wrap:wrap;flex:1 0 100%;
  margin-top:11px;}
.actions form{display:inline;margin:0;}
.btn{display:inline-block;font:inherit;font-size:.84rem;padding:7px 13px;
  border-radius:9px;cursor:pointer;text-decoration:none;
  border:1px solid var(--line);background:var(--card);color:var(--text);
  margin:0;}
.btn:hover{border-color:var(--muted);}
.btn.danger{color:var(--danger);}
.btn.primary{width:100%;margin-top:8px;padding:12px 18px;font-size:.95rem;
  font-weight:600;background:var(--accent);color:var(--accent-ink);
  border-color:transparent;}
.btn.primary:hover{filter:brightness(1.08);border-color:transparent;}
a{color:var(--accent);}
.empty{color:var(--muted);font-size:.9rem;background:var(--card);
  border:1px dashed var(--line);border-radius:var(--radius);padding:24px;
  text-align:center;}
.layout{display:block;}
.schedule-col{min-width:0;}
.form-col{margin-top:26px;}
details.addbox{margin:0;}
@media (min-width:900px){
  body{padding:28px 24px 64px;}
  .wrap{max-width:1000px;}
  li.msg{flex-wrap:nowrap;align-items:center;padding:12px 16px;}
  .msg-body{flex:1 1 auto;}
  .actions{flex:none;width:auto;margin:0 0 0 14px;}
}
summary.add-toggle{display:flex;align-items:center;justify-content:center;
  gap:9px;list-style:none;width:100%;font-size:.95rem;font-weight:600;
  padding:12px 16px;border-radius:10px;background:var(--accent);
  color:var(--accent-ink);border:1px solid transparent;cursor:pointer;}
summary.add-toggle::-webkit-details-marker{display:none;}
summary.add-toggle::marker{content:"";}
summary.add-toggle::before{content:"+";font-weight:700;font-size:1.05rem;}
summary.add-toggle:hover{filter:brightness(1.08);}
details.addbox[open] summary.add-toggle{background:var(--card);
  color:var(--text);border-color:var(--line);margin-bottom:10px;}
details.addbox[open] summary.add-toggle:hover{filter:none;
  border-color:var(--muted);}
details.addbox[open] summary.add-toggle::before{content:"–";}
form.card{background:var(--card);border:1px solid var(--line);
  border-radius:var(--radius);padding:16px;box-shadow:var(--shadow);}
label{display:block;font-size:.78rem;font-weight:600;color:var(--muted);
  margin:14px 0 5px;letter-spacing:.01em;}
input,select{width:100%;padding:11px 12px;border:1px solid var(--line);
  border-radius:10px;font:inherit;font-size:.95rem;background:var(--bg);
  color:var(--text);}
input:focus,select:focus{outline:2px solid var(--accent);outline-offset:1px;}
input[type=checkbox]{width:auto;margin:0;accent-color:var(--accent);}
.row{display:flex;gap:12px;flex-wrap:wrap;}
.row>div{flex:1;min-width:140px;}
.checkline{display:flex;align-items:center;gap:9px;font-weight:500;
  font-size:.9rem;color:var(--text);margin:0;cursor:pointer;}
.hint{color:var(--muted);font-size:.77rem;margin:6px 0 0;line-height:1.45;}
"""


def render_page(messages, edit_index=None, flash=None, flash_err=False):
    """Build the dashboard HTML (mobile-first, light/dark aware)."""
    e = html.escape

    cards = []
    for i, m in enumerate(messages):
        prio = m["priority"]
        prio_cls = ("prio-urgent" if prio == "urgent"
                    else "prio-high" if prio == "high" else "")
        chips = [f'<span class="chip">{e(describe_when(m))}</span>']
        if prio != "default":
            chips.append(f'<span class="chip {prio_cls}">{e(prio)}</span>')
        if m.get("quote"):
            label = (f'Hindi quote &middot; {e(m["quote"])}'
                     if isinstance(m["quote"], str) else "Hindi quote")
            chips.append(f'<span class="chip quote">+ {label}</span>')
        cards.append(f"""
<li class="msg">
  <div class="time">{e(m['time'])}</div>
  <div class="msg-body">
    <div class="msg-title">{e(m['title'])}</div>
    <div class="msg-text">{e(m['text'])}</div>
    <div class="chips">{''.join(chips)}</div>
  </div>
  <div class="actions">
    <form method="post" action="/test">
      <input type="hidden" name="id" value="{i}">
      <button class="btn" type="submit">Test</button>
    </form>
    <a class="btn" href="/edit?id={i}">Edit</a>
    <form method="post" action="/delete"
          onsubmit="return confirm('Delete this message?')">
      <input type="hidden" name="id" value="{i}">
      <button class="btn danger" type="submit">Delete</button>
    </form>
  </div>
</li>""")

    # edit form (blank for add, prefilled for edit)
    if edit_index is None:
        fm = {"time": "", "title": "", "text": "", "priority": "default"}
        days_mode, days_custom, heading = "everyday", "", "Add a message"
        hidden_id = ""
    else:
        m = messages[edit_index]
        fm = m
        days = m["days"]
        if isinstance(days, str):
            days_mode, days_custom = days, ""
        else:
            days_mode, days_custom = "custom", ", ".join(
                SHORT_DAY.get(d, d) for d in days)
        heading = f"Edit message #{edit_index + 1}"
        hidden_id = f'<input type="hidden" name="id" value="{edit_index}">'

    day_options = "".join(
        f'<option value="{v}"{" selected" if v == days_mode else ""}>{t}</option>'
        for v, t in [("everyday", "Every day"), ("weekdays", "Weekdays"),
                     ("weekends", "Weekends"), ("custom", "Specific days...")])
    prio_options = "".join(
        f'<option value="{p}"{" selected" if p == fm["priority"] else ""}>{p}</option>'
        for p in PRIORITIES)

    custom_display = "block" if days_mode == "custom" else "none"
    flash_html = (f'<div class="alert{" ok" if not flash_err else ""}">'
                  f'{e(flash)}</div>') if flash else ""
    warn = topic_warning()
    warn_html = f'<div class="alert">{e(warn)}</div>' if warn else ""
    persistence = ("GitHub" if GITHUB_REPO else
                   "ephemeral -- changes are lost on restart/redeploy")

    current_cat = fm.get("quote") if isinstance(fm.get("quote"), str) else ""
    quote_cat_options = "".join(
        f'<option value="{v}"{" selected" if v == current_cat else ""}>{t}</option>'
        for v, t in [("", "Any"), ("positive", "Positive"),
                     ("success", "Success"), ("love", "Love"),
                     ("attitude", "Attitude"),
                     ("motivational", "Motivational")])

    return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta name="color-scheme" content="light dark">
<title>Notifier schedule</title>
<style>{STYLE}</style></head>
<body><div class="wrap">
<header class="top">
 <h1>🔔 Message schedule</h1>
 <button class="theme-toggle" type="button" onclick="toggleTheme()"
   aria-label="Switch between light and dark mode" title="Light / dark">◐</button>
</header>
<p class="sub">{len(messages)} message(s) &middot; times in IST
 &middot; storage: {e(persistence)}</p>
{flash_html}
{warn_html}
<div class="layout">
<main class="schedule-col">
<ul class="schedule">
{''.join(cards) or '<li class="empty">No messages yet -- add one below.</li>'}
</ul>
</main>
<aside class="form-col">
<details class="addbox"{' open' if edit_index is not None else ''}>
<summary class="add-toggle">{e(heading)}</summary>
<form method="post" action="/save" class="card">
 {hidden_id}
 <div class="row">
  <div><label>Time (IST)</label>
   <input type="time" name="time" value="{e(fm['time'])}" required></div>
  <div><label>Priority</label>
   <select name="priority">{prio_options}</select></div>
 </div>
 <div class="row">
  <div><label>Days</label>
   <select name="days_mode" id="days_mode"
     onchange="document.getElementById('custom_days').style.display=
       this.value==='custom'?'block':'none'">{day_options}</select></div>
  <div id="custom_days" style="display:{custom_display}">
   <label>Specific days (e.g. mon, wed, fri)</label>
   <input name="days_custom" value="{e(days_custom)}"
     placeholder="mon, wed, fri"></div>
 </div>
 <label>Date (optional)</label>
 <input name="date" value="{e(fm.get('date', ''))}" maxlength="10"
   placeholder="MM-DD or YYYY-MM-DD">
 <p class="hint">MM-DD sends yearly on that date; YYYY-MM-DD sends once on that
 exact date and never again until you change it. The Days setting is ignored
 when a date is set.</p>
 <label>Title</label>
 <input name="title" value="{e(fm['title'])}" maxlength="60"
   placeholder="Bold heading in the notification">
 <label>Message text</label>
 <input name="text" value="{e(fm['text'])}" required maxlength="300"
   placeholder="What should the notification say?">
 <label class="checkline"><input type="checkbox" name="quote" value="1"{' checked' if fm.get('quote') else ''}>
  Append a daily Hindi quote</label>
 <label>Quote category</label>
 <select name="quote_category">{quote_cat_options}</select>
 <p class="hint">Random Hindi quote from hindi-quotes.vercel.app - pick a
 category or leave it on Any.</p>
 <button class="btn primary" type="submit">Save message</button>
</form>
</details>
</aside>
</div>
</div>
<script>
(function(){{
  try {{
    var saved = localStorage.getItem('notify-theme');
    if (saved) document.documentElement.setAttribute('data-theme', saved);
  }} catch (err) {{}}

  // Closing the panel returns it to "Add a message" mode: the label goes
  // back and the form is emptied, so saving then adds a new reminder
  // instead of updating the one you had opened for editing.
  try {{
    var box = document.querySelector('details.addbox');
    if (box) {{
      var form = box.querySelector('form');
      var summary = box.querySelector('summary');
      box.addEventListener('toggle', function () {{
        if (box.open || !form || !summary) return;
        var hidden = form.querySelector('input[name="id"]');
        if (hidden) hidden.remove();
        summary.textContent = 'Add a message';
        ['time', 'title', 'text', 'date', 'days_custom'].forEach(
          function (name) {{
            var el = form.querySelector('[name="' + name + '"]');
            if (el) el.value = '';
          }});
        var prio = form.querySelector('[name="priority"]');
        if (prio) prio.value = 'default';
        var days = form.querySelector('[name="days_mode"]');
        if (days) days.value = 'everyday';
        var custom = form.querySelector('#custom_days');
        if (custom) custom.style.display = 'none';
        var quote = form.querySelector('[name="quote"]');
        if (quote) quote.checked = false;
        var cat = form.querySelector('[name="quote_category"]');
        if (cat) cat.value = '';
      }});
    }}
  }} catch (err) {{}}
}})();
function toggleTheme(){{
  var root = document.documentElement;
  var current = root.getAttribute('data-theme');
  if (!current) {{
    current = window.matchMedia('(prefers-color-scheme: dark)').matches
      ? 'dark' : 'light';
  }}
  var next = current === 'dark' ? 'light' : 'dark';
  root.setAttribute('data-theme', next);
  try {{ localStorage.setItem('notify-theme', next); }} catch (err) {{}}
}}
</script>
</body></html>"""


def render_error(message):
    return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta name="color-scheme" content="light dark">
<title>Error</title><style>{STYLE}</style></head>
<body><div class="wrap">
<h2>Something went wrong</h2>
<p class="note">{html.escape(message)}</p>
<p><a href="/ui">&larr; Back to the schedule</a></p>
</div></body></html>"""


def parse_form(body_bytes):
    return {k: v[0] for k, v in
            urllib.parse.parse_qs(body_bytes.decode("utf-8")).items()}


def form_to_message(form):
    """Validate a submitted form. Returns (message, error)."""
    m = {
        "time": form.get("time", "").strip(),
        "title": form.get("title", "").strip() or "Reminder",
        "text": form.get("text", "").strip(),
        "priority": form.get("priority", "default"),
    }
    if not re.fullmatch(r"\d{1,2}:\d{2}", m["time"]):
        return None, "Please pick a valid time (HH:MM)."
    hour, minute = int(m["time"][:2]), int(m["time"][3:])
    if not (0 <= hour <= 23 and 0 <= minute <= 59):
        return None, "That time is out of range."
    if not m["text"]:
        return None, "Message text cannot be empty."
    if m["priority"] not in PRIORITIES:
        m["priority"] = "default"

    mode = form.get("days_mode", "everyday")
    if mode == "custom":
        days = [d.strip().lower() for d in
                re.split(r"[,\s]+", form.get("days_custom", ""))
                if d.strip()]
        days = [DAY_ALIASES.get(d, d) for d in days]
        bad = [d for d in days if d not in DAY_ORDER]
        if bad or not days:
            return None, ("Specific days must be from: mon, tue, wed, thu, "
                          "fri, sat, sun.")
        m["days"] = days
    else:
        if mode not in ("everyday", "weekdays", "weekends"):
            mode = "everyday"
        m["days"] = mode

    date_value = form.get("date", "").strip()
    if date_value:
        one_time = re.fullmatch(r"\d{4}-\d{2}-\d{2}", date_value)
        yearly = re.fullmatch(r"\d{2}-\d{2}", date_value)
        if not (one_time or yearly):
            return None, ("Date must be MM-DD (fires yearly) or "
                          "YYYY-MM-DD (fires once).")
        if one_time:
            year, mm, dd = (date_value[:4], date_value[5:7], date_value[8:])
        else:
            year, mm, dd = "2024", date_value[:2], date_value[3:]
        try:
            date_obj = datetime.date(int(year), int(mm), int(dd))
        except ValueError:
            return None, "That is not a real calendar date."
        if one_time and date_obj < datetime.datetime.now(IST).date():
            return None, ("That date is already in the past -- pick a "
                          "future date for a one-time reminder.")
        m["date"] = date_value
    if form.get("quote"):
        category = form.get("quote_category", "").strip().lower()
        m["quote"] = category if category in QUOTE_CATEGORIES else True
    return m, None


class AdminHandler(BaseHTTPRequestHandler):
    # ---------- helpers ----------
    def _authorized(self):
        if not ADMIN_PASSWORD:
            return True
        header = self.headers.get("Authorization", "")
        if header.startswith("Basic "):
            try:
                decoded = base64.b64decode(header[6:]).decode("utf-8")
                _, _, pw = decoded.partition(":")
                if hmac.compare_digest(pw, ADMIN_PASSWORD):
                    return True
            except Exception:  # noqa: BLE001
                pass
        self.send_response(401)
        self.send_header("WWW-Authenticate", 'Basic realm="Notifier"')
        self.send_header("Content-Type", "text/plain; charset=utf-8")
        self.end_headers()
        self.wfile.write(b"Unauthorized.\n")
        return False

    def _read_body(self):
        length = int(self.headers.get("Content-Length", 0))
        return self.rfile.read(length) if length else b""

    def _respond(self, content, status=200, content_type="text/html"):
        body = content.encode("utf-8") if isinstance(content, str) else content
        self.send_response(status)
        self.send_header("Content-Type",
                         f"{content_type}; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _redirect(self, flash=None, flash_err=False):
        target = "/ui"
        if flash:
            target += ("?err=1&msg=" if flash_err else
                       "?msg=") + urllib.parse.quote(flash)
        self.send_response(303)
        self.send_header("Location", target)
        self.send_header("Content-Length", "0")
        self.end_headers()

    # ---------- routes ----------
    def do_GET(self):  # noqa: N802
        parsed = urllib.parse.urlparse(self.path)
        if parsed.path == "/health":
            return self._respond("Notifier is running.\n",
                                 content_type="text/plain")
        if not self._authorized():
            return
        try:
            if parsed.path in ("/", "/index.html"):
                # tiny status line -- keep-alive pingers (cron-job.org)
                # reject responses over ~1 kB, so the root must stay small
                return self._respond(
                    "Notifier is running. Dashboard at /ui\n",
                    content_type="text/plain")
            if parsed.path == "/ui":
                qs = urllib.parse.parse_qs(parsed.query)
                flash = qs.get("msg", [None])[0]
                flash_err = "err" in qs
                return self._respond(render_page(load_messages(),
                                                  flash=flash,
                                                  flash_err=flash_err))
            if parsed.path == "/edit":
                qs = urllib.parse.parse_qs(parsed.query)
                try:
                    idx = int(qs.get("id", [""])[0])
                    messages = load_messages()
                    if not 0 <= idx < len(messages):
                        raise IndexError
                except (ValueError, IndexError):
                    return self._respond(render_error("No such message."),
                                         status=404)
                return self._respond(render_page(messages, edit_index=idx))
            return self._respond(render_error("Page not found."), status=404)
        except Exception as exc:  # noqa: BLE001
            self._respond(render_error(f"Could not load the schedule: "
                                       f"{exc}"), status=500)

    def do_POST(self):  # noqa: N802
        if not self._authorized():
            return
        parsed = urllib.parse.urlparse(self.path)
        try:
            form = parse_form(self._read_body())
            messages = load_messages()

            if parsed.path == "/save":
                msg, err = form_to_message(form)
                if err:
                    return self._redirect(err, flash_err=True)
                try:
                    idx = int(form.get("id", ""))
                    if not 0 <= idx < len(messages):
                        raise ValueError
                    messages[idx] = msg
                    action = f"updated message #{idx + 1}"
                except ValueError:
                    messages.append(msg)
                    action = "added the message"
                gh_err = save_messages(messages)
                return self._redirect(
                    f"{'Saved: ' + action}" +
                    (f" (warning: {gh_err})" if gh_err else ""),
                    flash_err=bool(gh_err))

            if parsed.path == "/delete":
                try:
                    idx = int(form.get("id", ""))
                    removed = messages.pop(idx)
                except (ValueError, IndexError):
                    return self._redirect("No such message.", flash_err=True)
                gh_err = save_messages(messages)
                return self._redirect(
                    f"Deleted '{removed.get('title', 'message')}'" +
                    (f" (warning: {gh_err})" if gh_err else ""),
                    flash_err=bool(gh_err))

            if parsed.path == "/test":
                try:
                    idx = int(form.get("id", ""))
                    m = messages[idx]
                except (ValueError, IndexError):
                    return self._redirect("No such message.", flash_err=True)
                ok, detail = send_notification(message_text(m),
                                               m["title"], m["priority"])
                return self._redirect(
                    "Test notification sent - check your phone!"
                    if ok else
                    f"Could not send: {detail}",
                    flash_err=not ok)

            return self._respond(render_error("Page not found."), status=404)
        except Exception as exc:  # noqa: BLE001
            self._respond(render_error(f"Something went wrong: {exc}"),
                         status=500)

    def log_message(self, fmt, *args):  # keep Render logs readable
        print(f"[web] {self.address_string()} {fmt % args}")


def main():
    boot_load()

    if SEND_STARTUP_MESSAGE:
        started_at = datetime.datetime.now(IST).strftime("%d %b %Y, %H:%M")
        send_notification(
            f"{STARTUP_MESSAGE} (started {started_at} IST)",
            title="Notifier started",
            tags="rocket",
        )

    if RUN_SCHEDULER:
        threading.Thread(target=run_scheduler, daemon=True).start()
    else:
        print("[scheduler] disabled (editor mode: this service only "
              "manages the schedule; another component sends it)")

    server = ThreadingHTTPServer(("0.0.0.0", PORT), AdminHandler)
    print(f"[web] interface + scheduler listening on port {PORT}")
    server.serve_forever()


if __name__ == "__main__":
    main()
