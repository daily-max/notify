#!/usr/bin/env python3
"""
Notifier Web -- your message scheduler on Render's FREE plan.

Why this file exists:
  Render's free compute only covers *web* services, and a free web service
  sleeps after 15 minutes without inbound traffic. So this program:
    1. runs a tiny HTTP server (so Render sees a web service), and
    2. runs the message scheduler in a background thread, and
    3. you keep it awake by pinging its URL every ~10 minutes with a free
       uptime pinger (cron-job.org or UptimeRobot).

Deploying (free):
  1. Push this file, messages.json, render.yaml and requirements.txt
     to a GitHub repo.
  2. On render.com: New + -> Web Service -> connect that repo.
     (With render.yaml in the repo root, settings are picked up
     automatically; just set the NTFY_TOPIC environment variable
     in the dashboard after creating the service.)
  3. Choose the Free instance type and deploy.
  4. Copy your service URL (https://your-app.onrender.com) and add it to
     cron-job.org (free) with a 10-minute schedule, so the service
     never sleeps.

Notification topic:
  Set the NTFY_TOPIC environment variable in the Render dashboard to the
  topic name you subscribed to in the ntfy phone app. (There is a default
  below if you prefer editing code instead.)

Times in messages.json are India time (Asia/Kolkata), so the schedule is
correct no matter what timezone Render's server runs in.

Requires only Python 3.9+ (standard library only).
"""

import datetime
import json
import os
import pathlib
import threading
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

DAY_ALIASES = {
    "mon": "monday", "tue": "tuesday", "wed": "wednesday",
    "thu": "thursday", "fri": "friday", "sat": "saturday", "sun": "sunday",
}


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
# Schedule handling
# ------------------------------------------------------------------
def load_messages():
    with open(MESSAGES_FILE, "r", encoding="utf-8") as fh:
        messages = json.load(fh)
    for index, msg in enumerate(messages):
        for field in ("time", "text"):
            if field not in msg:
                raise ValueError(
                    f"Message #{index + 1} in {MESSAGES_FILE.name} is "
                    f"missing the '{field}' field.")
        msg.setdefault("title", "Reminder")
        msg.setdefault("priority", "default")
        msg.setdefault("days", "everyday")
    return messages


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


def run_scheduler():
    """Check every 20 seconds and fire messages whose time has arrived."""
    messages = load_messages()
    print(f"[scheduler] loaded {len(messages)} message(s), "
          f"topic '{NTFY_TOPIC}', timezone Asia/Kolkata")

    fired = set()
    current_date = None

    while True:
        now = datetime.datetime.now(IST)
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

        import time as _time
        _time.sleep(20)


# ------------------------------------------------------------------
# Tiny web server (this is what makes Render treat it as a web service)
# ------------------------------------------------------------------
class HealthHandler(BaseHTTPRequestHandler):
    def do_GET(self):  # noqa: N802 - BaseHTTPRequestHandler API
        body = b"Notifier is running.\n"
        self.send_response(200)
        self.send_header("Content-Type", "text/plain; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_HEAD(self):  # noqa: N802
        self.send_response(200)
        self.end_headers()

    def log_message(self, fmt, *args):  # keep Render logs readable
        print(f"[web] {self.address_string()} {fmt % args}")


def main():
    threading.Thread(target=run_scheduler, daemon=True).start()

    server = ThreadingHTTPServer(("0.0.0.0", PORT), HealthHandler)
    print(f"[web] listening on port {PORT}")
    server.serve_forever()


if __name__ == "__main__":
    main()
