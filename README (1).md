# Weather Alert Bot — River Falls, WI

Checks tomorrow's forecast for River Falls, Wisconsin (44°51′31″N, 92°37′30″W)
using the free [Open-Meteo Forecast API](https://open-meteo.com/) (no API key
needed) and posts a Discord message when notable weather is expected.

## Alert conditions

An alert fires if **any** of the following are true for tomorrow:

| Condition | Threshold |
|---|---|
| Rain | probability ≥ 60% **and** total ≥ 0.01 in |
| Snow | total ≥ 0.1 in |
| Heat | high ≥ 95°F |
| Cold | low ≤ 32°F |

Only one alert is sent per forecast date — the date of the last successful
alert is recorded in `alert_state.json` so repeated runs (every 20 minutes)
don't spam the channel while the same condition remains true.

## Files

- `weather_alert.py` — main script
- `requirements.txt` — Python dependencies
- `.env.example` — template for local environment variables
- `.gitignore` — excludes `.env` and other local files from git
- `alert_state.json` — tracks the last forecast date an alert was sent for
- `.github/workflows/weather-alert.yml` — scheduled + manual GitHub Actions workflow

## 1. Install requirements

```bash
python -m venv .venv
source .venv/bin/activate   # on Windows: .venv\Scripts\activate
pip install -r requirements.txt
```

## 2. Configure your local `.env` file

```bash
cp .env.example .env
```

Edit `.env` and set `DISCORD_WEBHOOK_URL` to your Discord webhook URL:

1. In Discord, go to **Server Settings → Integrations → Webhooks**.
2. Create (or select) a webhook for the channel you want alerts in.
3. Copy the webhook URL and paste it into `.env`.

Leave `FORCE_ALERT=false` for normal use. `.env` is already excluded from git
via `.gitignore`, so it will never be committed.

> If you ever paste a real webhook URL somewhere it could be logged or
> shared (chat, screenshot, ticket, etc.), treat it as compromised and
> regenerate it from the same Discord settings page.

## 3. Test the bot locally

Load your `.env` file into the shell, then run the script. One simple way
without extra dependencies:

```bash
export $(grep -v '^#' .env | xargs)   # macOS/Linux
python weather_alert.py
```

On Windows PowerShell:

```powershell
Get-Content .env | ForEach-Object {
    if ($_ -match '^\s*([^#][^=]*)=(.*)$') {
        [System.Environment]::SetEnvironmentVariable($matches[1], $matches[2])
    }
}
python weather_alert.py
```

Expected output is one of:
- `No notable weather for <date>. No alert sent.`
- `Alert sent and alert_state.json updated for <date>.`
- `Conditions met for <date>, but an alert was already sent for this forecast date. Skipping to avoid duplicates.`

### Force a test alert

To confirm your webhook works end-to-end without waiting for real alert
conditions, set `FORCE_ALERT=true` for one run. This still pulls real
forecast data and sends a real Discord message, but it will **not** modify
`alert_state.json`:

```bash
FORCE_ALERT=true python weather_alert.py
```

(On Windows PowerShell: `$env:FORCE_ALERT="true"; python weather_alert.py`)

## 4. Add the webhook as a GitHub Actions secret

1. In your GitHub repository, go to **Settings → Secrets and variables → Actions**.
2. Click **New repository secret**.
3. Name: `DISCORD_WEBHOOK_URL`
4. Value: your Discord webhook URL.
5. Save.

The webhook URL is never printed by the script and never appears in the
workflow file — it's injected at runtime from this secret.

## 5. Run the workflow manually

1. Push this project to GitHub (with `alert_state.json` committed, `.env`
   excluded by `.gitignore`).
2. In the repo, go to the **Actions** tab and select **Weather Alert Bot**.
3. Click **Run workflow**. You can set the `force_alert` input to `true` for
   a one-off test, or leave it `false` for a normal check.
4. Check the run logs, and check Discord for the message.

Once confirmed, the workflow will run automatically every 20 minutes via the
`schedule` trigger. Any change to `alert_state.json` (i.e., a new alert being
sent) is committed back to the repository automatically so state persists
between runs.

## Notes

- GitHub Actions' `schedule` cron is not guaranteed to run at the exact
  minute during periods of high load; occasional short delays are normal.
- All temperatures are in Fahrenheit and precipitation in inches, as
  configured via the Open-Meteo API's `temperature_unit` and
  `precipitation_unit` parameters.
- The time zone used for forecast dates is `America/Chicago` (Central Time,
  which observes CST/CDT automatically).
