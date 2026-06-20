# Arma Mod Alert

Watches an Arma 3 mod preset for Steam Workshop updates and posts a Discord alert when any mod is updated.

## How it works

1. Reads mod IDs from an Arma 3 launcher HTML preset export
2. Polls the Steam Workshop API each hour
3. Posts a rich embed to a Discord webhook when a mod's update timestamp changes

## Setup

**1. Export your mod preset from the Arma 3 launcher**

In the launcher, go to Mods → Preset → Export → HTML. Save the file into this directory.

**2. Configure `config.json`**

```json
{
  "poll_interval_minutes": 60,
  "preset_file": "modlist.html",
  "alert_role_id": "your_role_id"
}
```

`alert_role_id` is optional — if set, the bot will tag that role in each alert. Leave blank to disable.

**3. Set your Discord webhook URL as an environment variable**

PowerShell:
```powershell
$env:DISCORD_WEBHOOK_URL="https://discord.com/api/webhooks/..."
```

**4. Install dependencies**

```
pip install requests
```

**5. Run**

```
# Single poll then exit (used by GitHub Actions)
python bot.py --once

# Continuous loop (local use)
python bot.py
```

## GitHub Actions (automated hourly checks)

The workflow at `.github/workflows/mod-watcher.yml` runs the bot every hour via GitHub Actions so you don't need a server.

1. Go to your repo → **Settings** → **Secrets and variables** → **Actions** → **New repository secret**
2. Name: `DISCORD_WEBHOOK_URL`, value: your webhook URL
3. Push — the workflow will trigger on schedule and can also be run manually from the Actions tab

The workflow commits `mod_state.json` and `pending_deletes.json` back to the repo after each run to persist state.

## State files

`mod_state.json` tracks the last known update timestamp for each mod ID. On first run, mods are baselined with no alert sent; subsequent updates trigger a Discord notification.

`pending_deletes.json` tracks posted alert message IDs and their scheduled deletion time. Each alert is automatically deleted from Discord after 7 days.
