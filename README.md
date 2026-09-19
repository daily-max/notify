# Phone Notifier on Render

Sends your custom messages (from `messages.json`) as real push
notifications to your phone via the free [ntfy](https://ntfy.sh) app,
running in the cloud on Render -- no always-on device of your own needed.

## One-time setup (ntfy app)

1. Install the free **ntfy** app on your phone (Play Store / App Store).
2. In the app: **+ Subscribe** and type a unique, hard-to-guess topic
   name, e.g. `my-alerts-x7k2pq`.
3. Remember that name -- you'll set it as the `NTFY_TOPIC` environment
   variable on Render.

## Option A -- Free (web service + keep-alive pings)

Render's free plan only covers *web* services, and they sleep after
15 minutes without traffic. `notifier_web.py` works around this:

1. Push these files to a **GitHub repo**:
   - `notifier_web.py`
   - `messages.json`
   - `requirements.txt`
   - `render.yaml`
2. On [render.com](https://render.com): **New + -> Blueprint**,
   connect the repo. When prompted for `NTFY_TOPIC`, enter your
   ntfy topic name. The blueprint uses the Free instance type.
3. After it deploys, copy your service URL
   (e.g. `https://phone-notifier-xxxx.onrender.com`).
4. Go to [cron-job.org](https://cron-job.org) (free) and create a job
   that calls your service URL **every 10 minutes**. This keeps the
   service awake so notifications fire on time.

Runs entirely on Render's free tier (a month has at most 744 hours,
within the 750 free instance hours Render grants each month).

## Option B -- ~$1/month (Render Cron Job, simplest & most reliable)

`send_due.py` runs, sends whatever is due, and exits -- designed for
Render's cron jobs:

1. Push these files to a **GitHub repo**:
   - `send_due.py`
   - `messages.json`
   - `requirements.txt`
2. On render.com: **New + -> Cron Job**, connect the repo.
3. Configure:
   - Runtime: **Python 3**
   - Build command: `pip install -r requirements.txt`
   - Command: `python send_due.py`
   - Schedule: `*/5 * * * *`  (every 5 minutes)
   - Instance type: Starter
4. Add environment variable `NTFY_TOPIC` = your ntfy topic name.
5. Create the job. Done.

Messages arrive within ~5 minutes of their scheduled time. One cron job
handles any number of messages, and the cost is the $1/month minimum.

## Editing your messages

All message times are **India time (IST)** -- no UTC conversion needed.
Each entry in `messages.json`:

```json
{
  "time": "07:30",
  "days": "everyday",
  "title": "Good morning",
  "text": "Time for your morning walk!",
  "priority": "high"
}
```

- `days`: `"everyday"`, `"weekdays"`, `"weekends"`, or a list like
  `["mon", "wed", "fri"]`
- `priority`: `min`, `low`, `default`, `high`, or `urgent`
- Push changes to GitHub and Render redeploys automatically.

## Testing

- Option A: open your service URL in a browser -- it should say
  "Notifier is running."
- Option B: in the cron job's **Runs** page, click **Trigger Run**.
- Either way, send yourself a test push from any computer:
  `curl -d "test ping" https://ntfy.sh/YOUR-TOPIC`

## Troubleshooting

- No notification? Check that `NTFY_TOPIC` on Render exactly matches the
  topic you subscribed to in the ntfy app (it's case-sensitive).
- Option A notifications late or missing? Your keep-alive pinger
  probably stopped -- check cron-job.org still pings your URL.
- Watch the logs: Render dashboard -> your service -> Logs.
