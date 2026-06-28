# One Piece Card Game — Tournament Event Monitor

Watches the official Bandai One Piece Card Game events page
(https://en.onepiece-cardgame.com/events/) across **all four sections**
(Championship, Official Events, Shop Events, Convention Events) and sends a
Discord webhook notification whenever a **new post tagged "Tournament"**
appears (this includes labels like `Tournament`, `Side Event Tournament`,
and `Official Shop Only Regularly Held Tournament`). Non-tournament posts
(plain Convention Events, Beginner/Pirates Party events, etc.) are ignored.

Runs for free on **GitHub Actions**, on a schedule, with no server required.

## How it works

- `monitor.py` fetches the events page, parses every event card, and checks
  which ones are tagged "Tournament".
- `seen.json` stores the list of event URLs already alerted on. After each
  run, the GitHub Action commits this file back to your repo, so state
  persists between runs.
- Only URLs not already in `seen.json` trigger a Discord notification.

## Setup (5 minutes)

### 1. Create a Discord webhook

1. In Discord, go to the server/channel you want alerts in.
2. Channel Settings → Integrations → Webhooks → **New Webhook**.
3. Name it (e.g. "OP Tournament Alerts"), copy the **Webhook URL**.

### 2. Create a GitHub repo

1. Go to https://github.com/new, create a new repository (can be private).
2. Upload all the files in this project to the repo, preserving the folder
   structure — specifically `.github/workflows/monitor.yml` must stay at
   that exact path.

   Easiest way: on your computer, `cd` into this project folder and run:
   ```bash
   git init
   git add .
   git commit -m "Initial commit"
   git branch -M main
   git remote add origin https://github.com/YOUR_USERNAME/YOUR_REPO.git
   git push -u origin main
   ```

### 3. Add your webhook URL as a secret

1. In your GitHub repo: **Settings → Secrets and variables → Actions →
   New repository secret**.
2. Name: `DISCORD_WEBHOOK_URL`
3. Value: paste the Discord webhook URL from step 1.
4. Save.

### 4. Enable the workflow

1. Go to the **Actions** tab in your repo. GitHub may ask you to confirm
   you want to enable workflows — click to enable.
2. The workflow runs automatically every 30 minutes. You can also trigger
   it immediately: Actions tab → "One Piece Tournament Monitor" →
   **Run workflow**.

That's it. The first run will mark every *currently listed* tournament as
"seen" without notifying (so you don't get blasted with every existing
event) — see note below. From then on, any new tournament post triggers a
Discord message.

> **Note on the first run:** The included `seen.json` starts empty, which
> means the very first run **will** notify you about every tournament
> currently on the page (so you get a one-time full list). If you'd rather
> skip that and only get notified about genuinely new posts going forward,
> run `python monitor.py` once locally (or manually trigger the Action once
> and ignore/mute the first batch), and the second run onward will only
> show real new posts.

## Toggling which sections are monitored

By default, this monitor watches **Championship, Official Events, and Shop
Events** for tournament posts — Convention Events are excluded by default.

To change this, open `.github/workflows/monitor.yml` and edit this line near
the top:

```yaml
env:
  INCLUDED_SECTIONS: "Championship,Official Events,Shop Events"
```

To also include Convention Events, change it to:

```yaml
  INCLUDED_SECTIONS: "Championship,Official Events,Shop Events,Convention Events"
```

Save, commit, and push — the next scheduled run (or a manual "Run workflow"
trigger) will pick up the change automatically. No need to touch
`monitor.py`.

> Note: events in a section you've excluded are never added to `seen.json`.
> So if you re-include Convention Events later, any tournament-tagged
> convention posts that already existed on the page at that time will be
> treated as "new" and trigger a one-time notification — same as the very
> first run.

## Adjusting the schedule

Edit the cron line in `.github/workflows/monitor.yml`:
```yaml
- cron: "*/30 * * * *"   # every 30 minutes
```
GitHub Actions free tier on public repos has no practical limit for this
usage; private repos get 2,000 free minutes/month, and each run takes
under a minute, so even 15-minute intervals are well within the free tier.

## Running it yourself instead (no GitHub Actions)

If you'd rather run this on your own machine/server on a cron job or
Windows Task Scheduler instead of GitHub Actions:

```bash
pip install -r requirements.txt
export DISCORD_WEBHOOK_URL="https://discord.com/api/webhooks/..."
export INCLUDED_SECTIONS="Championship,Official Events,Shop Events"  # optional; omit to include all four
python monitor.py
```

Then schedule that command to run periodically (e.g. `crontab -e`:
`*/30 * * * * cd /path/to/op-monitor && DISCORD_WEBHOOK_URL=... python3 monitor.py`).

## Files

- `monitor.py` — the scraper + notifier
- `requirements.txt` — Python dependencies
- `seen.json` — persisted state (auto-updated by the workflow)
- `.github/workflows/monitor.yml` — the scheduled GitHub Action
