#!/usr/bin/env python3
"""
Notifier Web v3 -- message scheduler + admin interface on Render's FREE plan.

What's new in v3:
  * A web interface at your service URL: add, edit, delete and test-fire
    messages from your phone or laptop -- no code edits needed.
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
NTFY_SERVER = "https://ntfy.sh"
NTFY_TOPIC = os.environ.get("NTFY_TOPIC", "my-alerts-x7k2pq")  # <-- CHANGE
MESSAGES_FILE = pathlib.Path(__file__).resolve().parent / "messages.json"
PORT = int(os.environ.get("PORT", "10000"))

STARTUP_MESSAGE = os.environ.get(
    "START_MESSAGE", "Notifier started - your schedule is live.")
SEND_STARTUP_MESSAGE = os.environ.get("SEND_STARTUP_MESSAGE", "1") != "0"
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
    """POST a message to the ntfy topic. Returns True on success."""
    request = urllib.request.Request(
        f"{NTFY_SERVER}/{NTFY_TOPIC}",
        data=text.encode("utf-8"),
        method="POST",
        headers={"Title": title, "Priority": priority, "Tags": tags},
    )
    try:
        with urllib.request.urlopen(request, timeout=10) as response:
            ok = 200 <= response.status < 300
            print(f"[sent] {title}: {text} (HTTP {response.status})")
            return ok
    except Exception as exc:  # noqa: BLE001 - report any network error
        print(f"[ERROR] Could not send '{title}': {exc}")
        return False


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
            if (msg["time"] == hhmm and key not in fired
                    and day_matches(msg["days"], today_name)):
                send_notification(msg["text"], msg["title"], msg["priority"])
                fired.add(key)

        time.sleep(20)


# ------------------------------------------------------------------
# Web interface
# ------------------------------------------------------------------
STYLE = """
body{font-family:system-ui,-apple-system,'Segoe UI',sans-serif;background:
#f4f5f7;color:#1f2933;margin:0;padding:24px;}
.wrap{max-width:760px;margin:0 auto;}
h1{font-size:1.5rem;margin:0 0 4px;}
h2{font-size:1.15rem;margin:28px 0 12px;}
.note{color:#52606d;font-size:.9rem;margin:4px 0 18px;}
table{width:100%;border-collapse:collapse;background:#fff;border-radius:8px;
overflow:hidden;box-shadow:0 1px 3px rgba(0,0,0,.12);}
th,td{padding:10px 12px;text-align:left;border-bottom:1px solid #e4e7eb;
font-size:.92rem;vertical-align:top;}
th{background:#eef1f5;font-size:.8rem;text-transform:uppercase;
letter-spacing:.04em;color:#52606d;}
tr:last-child td{border-bottom:none;}
.time{font-weight:600;white-space:nowrap;}
.title{font-weight:600;}
form.card{background:#fff;border-radius:8px;padding:18px;margin-top:12px;
box-shadow:0 1px 3px rgba(0,0,0,.12);}
label{display:block;font-size:.82rem;font-weight:600;color:#52606d;
margin:12px 0 4px;}
input,select{width:100%;padding:9px 10px;border:1px solid #cbd2d9;
border-radius:6px;font-size:1rem;box-sizing:border-box;background:#fff;}
.row{display:flex;gap:12px;flex-wrap:wrap;}
.row>div{flex:1;min-width:130px;}
button{margin-top:16px;padding:10px 18px;border:none;border-radius:6px;
background:#2563eb;color:#fff;font-size:.95rem;cursor:pointer;}
button:hover{background:#1d4ed8;}
.linkbtn{background:none;border:none;color:#2563eb;padding:4px 6px;
margin:0;cursor:pointer;font-size:.85rem;text-decoration:underline;}
.danger{color:#dc2626;}
.err{background:#fef2f2;border:1px solid #fecaca;color:#991b1b;
padding:10px 12px;border-radius:6px;margin-bottom:14px;font-size:.9rem;}
.ok{background:#f0fdf4;border:1px solid #bbf7d0;color:#166534;
padding:10px 12px;border-radius:6px;margin-bottom:14px;font-size:.9rem;}
.prio-urgent{color:#dc2626;font-weight:600;}
.prio-high{color:#ea580c;}
"""


def render_page(messages, edit_index=None, flash=None, flash_err=False):
    """Build the dashboard HTML."""
    e = html.escape

    # rows
    rows = []
    for i, m in enumerate(messages):
        prio_cls = "prio-urgent" if m["priority"] == "urgent" else (
            "prio-high" if m["priority"] == "high" else "")
        rows.append(f"""
<tr>
 <td class="time">{e(m['time'])}</td>
 <td>{e(describe_days(m['days']))}</td>
 <td><span class="title">{e(m['title'])}</span><br>
     <span class="note">{e(m['text'])}</span></td>
 <td class="{prio_cls}">{e(m['priority'])}</td>
 <td style="white-space:nowrap">
   <form method="post" action="/test" style="display:inline">
     <input type="hidden" name="id" value="{i}">
     <button class="linkbtn" type="submit">Test</button>
   </form>
   <a href="/edit?id={i}">Edit</a> &middot;
   <form method="post" action="/delete" style="display:inline"
         onsubmit="return confirm('Delete this message?')">
     <input type="hidden" name="id" value="{i}">
     <button class="linkbtn danger" type="submit">Delete</button>
   </form>
 </td>
</tr>""")

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
    flash_html = (f'<div class="{"err" if flash_err else "ok"}">'
                  f'{e(flash)}</div>') if flash else ""
    persistence = ("GitHub" if GITHUB_REPO else
                    "ephemeral -- changes are lost on restart/redeploy")

    return f"""<!doctype html>
<html><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Notifier schedule</title>
<style>{STYLE}</style></head>
<body><div class="wrap">
<h1>🔔 Message schedule</h1>
<p class="note">{len(messages)} message(s) &middot; times shown in IST
 &middot; storage: {e(persistence)}</p>
{flash_html}
<table>
<tr><th>Time</th><th>Days</th><th>Message</th><th>Priority</th>
<th></th></tr>
{''.join(rows) or '<tr><td colspan="5" class="note">No messages yet -- add one below.</td></tr>'}
</table>

<h2>{e(heading)}</h2>
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
 <label>Title (bold heading in the notification)</label>
 <input name="title" value="{e(fm['title'])}" maxlength="60">
 <label>Message text</label>
 <input name="text" value="{e(fm['text'])}" required maxlength="300">
 <button type="submit">Save message</button>
</form>
</div></body></html>"""


def render_error(message):
    return f"""<!doctype html><html><head><meta charset="utf-8">
<title>Error</title><style>{STYLE}</style></head>
<body><div class="wrap"><h2>Something went wrong</h2>
<p class="note">{html.escape(message)}</p>
<p><a href="/">&larr; Back to the schedule</a></p>
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
        target = "/"
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
                ok = send_notification(m["text"], m["title"], m["priority"])
                return self._redirect(
                    "Test notification sent - check your phone!"
                    if ok else
                    "Could not send (check topic name / ntfy server).",
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

    threading.Thread(target=run_scheduler, daemon=True).start()

    server = ThreadingHTTPServer(("0.0.0.0", PORT), AdminHandler)
    print(f"[web] interface + scheduler listening on port {PORT}")
    server.serve_forever()


if __name__ == "__main__":
    main()
