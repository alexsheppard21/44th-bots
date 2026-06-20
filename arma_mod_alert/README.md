# Arma Mod Alert

Watches an Arma 3 mod preset for Steam Workshop updates and posts a Discord alert when any mod is updated.

## How it works

1. Reads mod IDs from an Arma 3 launcher HTML preset export
2. Polls the Steam Workshop API each hour
3. Posts a rich embed to a Discord webhook when a mod's update timestamp changes

## Setup

**1. Export your mod preset from the Arma 3 launcher**

In the launcher, go to Mods → Preset → Export → HTML. Save the file into this directory.

**2. Configure**

```bash
cp config.example.json config.json
```

Edit `config.json`:

```json
{
  "discord_webhook_url": "https://discord.com/api/webhooks/...",
  "poll_interval_minutes": 60,
  "preset_file": "your_preset.html",
  "alert_role_id": ""
}
```

`alert_role_id` is optional — if set, the bot will tag that role in each alert. Leave blank to disable.

**3. Install dependencies**

```bash
pip install requests
```

**4. Run**

```bash
# Continuous loop (local use)
python bot.py

# Single poll then exit (used by GitHub Actions)
python bot.py --once
```

## GitHub Actions (automated hourly checks)

The workflow in `mod-watcher.yml` runs the bot every hour via GitHub Actions so you don't need a server.

1. Copy `mod-watcher.yml` to `.github/workflows/mod-watcher.yml` in your repo
2. Add your Discord webhook URL as a repository secret named `DISCORD_WEBHOOK_URL`
3. Push — the workflow will trigger on schedule and can also be run manually from the Actions tab

The workflow commits `mod_state.json` back to the repo after each run to persist the last-seen update timestamps.

## State files

`mod_state.json` tracks the last known update timestamp for each mod ID. On first run, mods are baselined with no alert sent; subsequent updates trigger a Discord notification.

`pending_deletes.json` tracks posted alert message IDs and their scheduled deletion time. Each alert is automatically deleted from Discord after 7 days.
