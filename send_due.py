#!/usr/bin/env python3
"""
Send Due -- one-shot sender for a Render Cron Job (paid, ~$1/month).

Render cron jobs run a command on a schedule and exit when it finishes.
This script checks messages.json, sends every message whose scheduled
time (IST) falls within the last WINDOW_MINUTES, and exits.

Set up (renders ~$1/month for one cron job, total):
  1. Push this file, messages.json and requirements.txt to a GitHub repo.
  2. On render.com: New + -> Cron Job -> connect the repo.
  3. Runtime: Python 3
     Build command:  pip install -r requirements.txt
     Command:        python send_due.py
     Schedule:       */5 * * * *        (every 5 minutes, any timezone)
  4. Add an environment variable NTFY_TOPIC = your-ntfy-topic-name.
  5. Create the job (choose the Starter instance type).

Accuracy: messages arrive within ~5 minutes of their scheduled time.
Each message is sent only once per day because the script only looks
BACKWARD in time (scheduled between now-WINDOW and now).

Requires only Python 3.9+ (standard library only).
"""

import datetime
import json
import os
import pathlib
import urllib.request
from zoneinfo import ZoneInfo

# ------------------------------------------------------------------
# Config
# ------------------------------------------------------------------
IST = ZoneInfo("Asia/Kolkata")
NTFY_SERVER = "https://ntfy.sh"
NTFY_TOPIC = os.environ.get("NTFY_TOPIC", "my-alerts-x7k2pq")  # <-- CHANGE
MESSAGES_FILE = pathlib.Path(__file__).resolve().parent / "messages.json"
WINDOW_MINUTES = int(os.environ.get("WINDOW_MINUTES", "5"))

DAY_ALIASES = {
    "mon": "monday", "tue": "tuesday", "wed": "wednesday",
    "thu": "thursday", "fri": "friday", "sat": "saturday", "sun": "sunday",
}


def send_notification(text, title="Reminder", priority="default",
                      tags="bell"):
    """POST a message to the ntfy topic. Returns True on success."""
    request = urllib.request.Request(
        f"{NTFY_SERVER}/{NTFY_TOPIC}",
        data=text.encode("utf-8"),
        method="POST",
        headers={"Title": title, "Priority": priority, "Tags": tags},
    )
    with urllib.request.urlopen(request, timeout=10) as response:
        ok = 200 <= response.status < 300
        print(f"[sent] {title}: {text} (HTTP {response.status})")
        return ok


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


def main():
    now = datetime.datetime.now(IST)
    today_name = now.strftime("%A").lower()
    window_start = now - datetime.timedelta(minutes=WINDOW_MINUTES)
    sent_any = False

    with open(MESSAGES_FILE, "r", encoding="utf-8") as fh:
        messages = json.load(fh)

    for msg in messages:
        try:
            hour, minute = (int(part) for part in msg["time"].split(":"))
        except (KeyError, ValueError):
            print(f"[skip] bad time value: {msg.get('time')!r}")
            continue
        if "text" not in msg:
            print(f"[skip] message at {msg['time']} has no text")
            continue

        scheduled = now.replace(hour=hour, minute=minute,
                                 second=0, microsecond=0)

        if "date" in msg:
            # dated reminder: MM-DD fires yearly, YYYY-MM-DD fires once;
            # the Days setting is ignored when a date is set
            d = str(msg["date"])
            day_ok = (now.strftime("%Y-%m-%d") == d if len(d) == 10
                      else now.strftime("%m-%d") == d)
        else:
            day_ok = day_matches(msg.get("days", "everyday"), today_name)

        if window_start < scheduled <= now and day_ok:
            send_notification(
                message_text(msg),
                msg.get("title", "Reminder"),
                msg.get("priority", "default"),
            )
            sent_any = True

    if not sent_any:
        print(f"[info] nothing due in the last "
              f"{WINDOW_MINUTES} minutes (now {now:%H:%M} IST)")


if __name__ == "__main__":
    main()
