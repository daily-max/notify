# Phone Notifier on Render

Sends your custom messages (from `messages.json`) as real push
notifications to your phone via the free [ntfy](https://ntfy.sh) app,
running in the cloud on Render -- no always-on device of your own needed.

All times are **India time (IST)**. Standard library only: nothing to
install, no dependencies.

---

## One-time setup (ntfy app)

1. Install the free **ntfy** app on your phone (Play Store / App Store).
2. In the app: **+ Subscribe** and type a unique, hard-to-guess topic
   name, e.g. `my-alerts-x7k2pq`.
3. Remember that name -- you'll set it as the `NTFY_TOPIC` environment
   variable on Render. It is case-sensitive.

> Topics are not password-protected: anyone who knows the name can send to
> it or read from it. Pick something unguessable, and always set
> `NTFY_TOPIC` -- both scripts fall back to a placeholder topic that is
> visible in this public repo.

---

## Running it

Two deployment options. Either way, Render redeploys automatically when you
push to this repo.

### Option A -- Free (web service + keep-alive pings)

Render's free plan only covers *web* services, and they sleep after
15 minutes without traffic. `notifier_web.py` works around this:

1. On [render.com](https://render.com): **New + -> Blueprint**, connect
   this repo. When prompted for `NTFY_TOPIC`, enter your ntfy topic name.
   The blueprint (`render.yaml`) uses the Free instance type.
2. After it deploys, copy your service URL
   (e.g. `https://phone-notifier-xxxx.onrender.com`).
3. Go to [cron-job.org](https://cron-job.org) (free) and create a job that
   calls your service URL **every 10 minutes**. This keeps the service
   awake so notifications fire on time.

Runs entirely on Render's free tier (a month has at most 744 hours, within
the 750 free instance hours Render grants each month).

### Option B -- ~$1/month (Render Cron Job, simplest & most reliable)

`send_due.py` runs, sends whatever is due, and exits:

1. On render.com: **New + -> Cron Job**, connect this repo.
2. Configure:
   - Runtime: **Python 3**
   - Build command: `pip install -r requirements.txt`
   - Command: `python send_due.py`
   - Schedule: `*/5 * * * *`  (every 5 minutes)
   - Instance type: Starter
3. Add environment variable `NTFY_TOPIC` = your ntfy topic name.
4. Create the job. Done.

Messages arrive within ~5 minutes of their scheduled time. One cron job
handles any number of messages, and the cost is the $1/month minimum.

---

## The dashboard

`notifier_web.py` serves a web interface -- the easiest way to manage your
schedule, no file editing required. Open:

```
https://YOUR-SERVICE-URL/ui
```

From there you can add, edit, delete and **Test** any reminder (Test sends
it immediately so you can confirm your phone receives it). The list shows
each reminder as a card with its time, text, day rules and priority; the
add/edit form is collapsed behind a "+ Add a message" button and resets
itself when closed. The theme follows your device, with a toggle to
override it.

If you set `ADMIN_PASSWORD`, the dashboard asks for it (HTTP Basic auth) --
worth doing, since anyone with the URL could otherwise edit your schedule.

Other routes, for reference:

| Route | What it does |
|---|---|
| `/` | tiny status line, kept small for keep-alive pingers |
| `/health` | plain-text health check |
| `/ui` | the dashboard |
| `/edit?id=N` | open the form on message N |
| `/save`, `/delete`, `/test` | POST endpoints used by the dashboard |

**Persistence:** Render's free instances have ephemeral storage, so edits
made in the dashboard are lost on restart unless you enable GitHub
persistence -- set `GITHUB_REPO` (`user/repo`) and `GITHUB_TOKEN` (a
fine-grained PAT with *Contents: Read/Write*). Every change is then
committed back to this repo, and appears in history as
"Update schedule from notifier UI".

---

## Editing your messages

Each entry in `messages.json` looks like this:

```json
{
  "time": "07:30",
  "days": "everyday",
  "title": "Good morning",
  "text": "Time for your morning walk!",
  "priority": "high"
}
```

| Field | Required | Notes |
|---|---|---|
| `time` | yes | `HH:MM`, India time |
| `text` | yes | the notification body |
| `title` | no | bold heading; defaults to "Reminder" |
| `days` | no | `"everyday"`, `"weekdays"`, `"weekends"`, or a list like `["mon","wed","fri"]`; defaults to everyday |
| `priority` | no | `min`, `low`, `default`, `high`, `urgent` |
| `date` | no | `"MM-DD"` fires **every year** on that date; `"YYYY-MM-DD"` fires **once**, then never again. When set, `days` is ignored. |
| `quote` | no | `true` for a random Hindi quote, or a category: `positive`, `success`, `love`, `attitude`, `motivational` |

Push changes to GitHub and Render redeploys automatically. The quote comes
from [hindi-quotes.vercel.app](https://hindi-quotes.vercel.app/random) and
is best-effort: if it can't be fetched, the reminder still sends.

---

## Environment variables

| Variable | Used by | Purpose |
|---|---|---|
| `NTFY_TOPIC` | both | the topic your phone is subscribed to (**set this**) |
| `NTFY_SERVER` | both | default `https://ntfy.sh`; point at a self-hosted server |
| `NTFY_TOKEN` | both | optional ntfy access token |
| `WINDOW_MINUTES` | `send_due.py` | how far back it looks for due messages (default 5) |
| `ADMIN_PASSWORD` | `notifier_web.py` | password-protect the dashboard |
| `GITHUB_REPO`, `GITHUB_TOKEN` | `notifier_web.py` | enable committing edits back to the repo |
| `GITHUB_BRANCH`, `GITHUB_PATH` | `notifier_web.py` | defaults `main` / `messages.json` |
| `SEND_STARTUP_MESSAGE` | `notifier_web.py` | set to `0` to skip the "Notifier started" push |
| `START_MESSAGE` | `notifier_web.py` | text of that startup push |
| `RUN_SCHEDULER` | `notifier_web.py` | set to `0` for editor mode (dashboard only; a cron job sends) |
| `PORT` | `notifier_web.py` | default `10000` |

---

## Testing

- Option A: open your service URL -- it should say "Notifier is running."
- Option B: in the cron job's **Runs** page, click **Trigger Run**.
- Either way: use the **Test** button in the dashboard, or send yourself a
  test push from any computer:
  `curl -d "test ping" https://ntfy.sh/YOUR-TOPIC`

---

## Troubleshooting

- **No notification?** Check that `NTFY_TOPIC` on Render exactly matches the
  topic you subscribed to in the ntfy app (it's case-sensitive), and that
  the dashboard isn't showing a warning banner about it.
- **"Could not send: HTTP 429 ... daily message quota reached"** --
  ntfy.sh's free daily quota is counted per source IP, and Render's free
  tier shares its egress IPs with many other apps, so the quota is
  typically exhausted by other people's traffic long before your few daily
  messages. Changing the topic or retrying will not help. Options:
  1. **ntfy.sh paid plan**: sign up, create an access token
     (Account -> Access tokens), then set `NTFY_TOKEN` on Render to
     `tk_...`. Paid users get a per-account quota, so the shared IP no
     longer matters.
  2. **Self-host ntfy**: run your own ntfy server (no quotas) on any
     machine/container you control, then set `NTFY_SERVER` on Render to its
     URL and subscribe in the phone app via
     "+ Subscribe -> use another server".
  3. Use a different delivery channel that has no shared-IP quota.
- **A reminder was skipped entirely?** The web scheduler fires a message
  only during its exact minute, so a redeploy or a sleeping service can
  miss it for that day. Option B's look-back window is more forgiving.
- **Option A notifications late or missing?** Your keep-alive pinger
  probably stopped -- check cron-job.org still pings your URL.
- **Watch the logs:** Render dashboard -> your service -> Logs.

---

## Files

| File | What it is |
|---|---|
| `messages.json` | your schedule |
| `notifier_web.py` | scheduler + dashboard (Option A) |
| `send_due.py` | one-shot sender (Option B) |
| `render.yaml` | Render Blueprint for the free web service |
| `requirements.txt` | intentionally empty (standard library only) |
| `ROADMAP.md` | current state, limitations and next steps |

Requires only Python 3.9+ (standard library only). On Windows, run
`pip install tzdata` once so the IST timezone is available.
