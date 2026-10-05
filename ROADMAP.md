# Roadmap — Phone Notifier

A cloud-hosted reminder system. A list of timed messages in `messages.json`
is pushed to your phone as notifications by a small Python service running
on Render. All times are India time (IST). Standard library only — no
dependencies to install, no always-on device of your own.

---

## 1. How it works

```
messages.json  ──►  scheduler  ──►  delivery channel  ──►  phone
                       │                    │
              notifier_web.py           ntfy.sh
              (or send_due.py)
```

1. **The schedule** lives in `messages.json`: what to say, at what time, on
   which days, at what priority, optionally with a date and a Hindi quote.
2. **A scheduler decides what is due.** Two implementations exist:
   - `notifier_web.py` runs continuously and checks every ~20 seconds while
     the web service is awake.
   - `send_due.py` runs once, sends whatever fell due in the last few
     minutes, and exits — built for a Render Cron Job.
3. **Delivery.** The message is POSTed to ntfy, which pushes it to the ntfy
   app on your phone.

---

## 2. Files

| File | Role |
|---|---|
| `messages.json` | The schedule. The only file you normally edit (via the dashboard or by hand). |
| `notifier_web.py` | Main program: scheduler **plus** the web dashboard at `/ui`. What the live service runs. |
| `send_due.py` | One-shot sender for a Render Cron Job. Sends what is due, then exits. |
| `render.yaml` | Render Blueprint for the free web-service deployment. |
| `requirements.txt` | Intentionally empty — standard library only. Exists so Render's Python build has something to install. |
| `README.md` | Setup, usage and troubleshooting. |
| `ROADMAP.md` | This file. |

---

## 3. Features that are live

**Dashboard (`/ui`)** — add, edit, delete and test-fire reminders from any
browser. Mobile shows a card list; desktop a wider list with actions at the
right edge. Automatic light/dark theme with a manual toggle. The add/edit
form sits behind a "+ Add a message" button and resets to add-mode when
closed. Optional password protection via `ADMIN_PASSWORD`.

**Schedule rules per message** — time of day, day rules, priority, an
optional date for yearly or one-time reminders, and an optional quote.

**Hindi quotes** — any reminder can append a random quote from
`hindi-quotes.vercel.app`, optionally from a category (positive, success,
love, attitude, motivational). Quote fetching is best-effort: if the API is
unreachable the reminder still sends, just without the quote.

**Dates** — `MM-DD` fires every year on that date; `YYYY-MM-DD` fires once
and never again. The Days setting is ignored when a date is present.

**Delivery diagnostics** — the dashboard shows the real reason a send
failed (e.g. `HTTP 429 ... daily message quota reached`), and warns when
`NTFY_TOPIC` looks unset or malformed.

**GitHub persistence** — with `GITHUB_REPO` and `GITHUB_TOKEN` set, edits
made in the dashboard are committed back to this repo, so they survive a
redeploy. (Visible in history as "Update schedule from notifier UI".)

---

## 4. Message schema

```json
{
  "time": "07:30",
  "days": "everyday",
  "title": "Morning walk",
  "text": "Time for your morning walk!",
  "priority": "high",
  "date": "03-14",
  "quote": "motivational"
}
```

| Field | Required | Values |
|---|---|---|
| `time` | yes | `HH:MM` in IST |
| `text` | yes | the notification body |
| `title` | no | bold heading (default "Reminder") |
| `days` | no | `everyday`, `weekdays`, `weekends`, or a list like `["mon","wed","fri"]` (default `everyday`) |
| `priority` | no | `min`, `low`, `default`, `high`, `urgent` (default `default`) |
| `date` | no | `MM-DD` = yearly; `YYYY-MM-DD` = one-time. Overrides `days`. |
| `quote` | no | `true` for any Hindi quote, or a category name |

---

## 5. Deployment

**Option A — free.** Render free *web* service running `notifier_web.py`,
kept awake by a free cron-job.org ping to the service URL every 10 minutes
(free services sleep after 15 minutes of no traffic). Fits inside Render's
750 free instance hours a month.

**Option B — about $1/month.** Render **Cron Job** running `send_due.py`
every 5 minutes (`*/5 * * * *`), Starter instance. Simpler and more
reliable: no sleeping, no keep-alive pinger, and it tolerates a missed run
better. Messages arrive within ~5 minutes of their scheduled time.

---

## 6. Configuration

Read by `notifier_web.py`:

| Variable | Purpose |
|---|---|
| `NTFY_TOPIC` | the topic your phone is subscribed to (**set this**) |
| `NTFY_SERVER` | default `https://ntfy.sh`; point at a self-hosted server |
| `NTFY_TOKEN` | optional ntfy access token (per-account quota on a paid plan) |
| `ADMIN_PASSWORD` | if set, the dashboard asks for this password (HTTP Basic) |
| `GITHUB_REPO` / `GITHUB_TOKEN` | enable committing schedule edits back to the repo |
| `GITHUB_BRANCH` / `GITHUB_PATH` | defaults `main` / `messages.json` |
| `SEND_STARTUP_MESSAGE` | set to `0` to disable the "Notifier started" push |
| `START_MESSAGE` | text of that startup push |
| `RUN_SCHEDULER` | set to `0` for editor mode (the UI manages the schedule; something else sends) |
| `PORT` | default `10000` |

Read by `send_due.py`: `NTFY_TOPIC`, `NTFY_SERVER`, `NTFY_TOKEN`,
`WINDOW_MINUTES` (default `5` — how far back it looks for due messages).

---

## 7. Known limitations

1. **Delivery is the open problem.** ntfy.sh's free daily quota is counted
   per *source IP*, and Render's free tier shares egress IPs with many
   other apps, so the quota is usually exhausted by other people's traffic
   before a handful of daily messages. Changing the topic or retrying does
   not help. This is the single thing standing between the project and
   reliably working notifications.
2. **Exact-minute matching** in the web scheduler: a reminder fires only if
   the service is awake during that exact minute. A redeploy or a sleeping
   service silently skips it for the day. `send_due.py`'s look-back window
   does not have this flaw.
3. **Fallback topic is public.** Both scripts fall back to a placeholder
   topic if `NTFY_TOPIC` is unset; that name is visible in this public
   repo, and ntfy topics are unauthenticated, so anyone who knows it can
   send to (or read from) it. Always set `NTFY_TOPIC` to something unique.
4. **Inconsistent fallback topic:** `send_due.py` has `my-alerts-x7k2pqr`
   (stray "r") while `notifier_web.py` has `my-alerts-x7k2pq`, so the two
   would send to different topics when the variable is unset.
5. **Documentation drift:** the README described the original file-only
   workflow long after the dashboard and the newer features landed.

---

## 8. Next steps

**Now — unblock delivery.** Pick one:
- *Paid ntfy plan* (~$5–6/month): create an access token and set
  `NTFY_TOKEN`; the quota then applies per account rather than per IP. No
  code changes.
- *Self-host ntfy*: no quotas, but needs an always-on server; point
  `NTFY_SERVER` at it.
- *Alternative channel*: a free channel with no shared-IP quota (a Telegram
  bot route was built, tested and then removed at the owner's request; it
  is about 60 lines and can be rebuilt quickly).

**Next — reliability.**
- Make the web scheduler forgiving of a missed minute (fire anything due
  since the last check), closing limitation 2.
- Fix the fallback-topic inconsistency (limitation 4).

**Later — polish.**
- Refresh the README so it documents the dashboard, quotes and dates
  (limitation 5).
- Delete the merged branches (`frontend`, `fix-toggle-sign`,
  `reset-on-close`) once they are no longer wanted.
- Consider a small "recent sends" panel on the dashboard, so a day with no
  notifications is diagnosable at a glance.

---

## 9. History (short version)

Original file-based notifier on Render → ntfy env-var support and real
error reporting → Hindi quotes and yearly/one-time dates → dashboard
redesign (cards, dark mode) → desktop layout refinements → bug fixes (the
toggle-button sign, and the panel resetting to add-mode when closed).
