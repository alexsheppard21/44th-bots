"""
Arma 3 Mod Update Watcher — Discord Alert Bot
Reads mod IDs from an Arma 3 launcher HTML preset export, polls the Steam
Workshop API, and posts to Discord when any mod is updated.

Run modes:
  python bot.py          # continuous loop (local use)
  python bot.py --once   # single poll then exit (GitHub Actions)
"""

import json
import os
import re
import sys
import time
import logging
import requests
from datetime import datetime, timezone
from pathlib import Path

# ── Config ────────────────────────────────────────────────────────────────────
CONFIG_FILE = Path(__file__).parent / "config.json"
STATE_FILE  = Path(__file__).parent / "mod_state.json"

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-8s  %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger("mod-watcher")

# ── Preset parser ─────────────────────────────────────────────────────────────
def load_preset(preset_path: Path) -> list[str]:
    """Parse an Arma 3 launcher HTML preset and return a list of Workshop mod IDs."""
    html = preset_path.read_text(encoding="utf-8", errors="ignore")
    # Each mod appears twice in the HTML (href attr + link text), so deduplicate
    # while preserving order
    ids = list(dict.fromkeys(re.findall(
        r'steamcommunity\.com/sharedfiles/filedetails/\?id=(\d+)',
        html
    )))
    if not ids:
        raise ValueError(f"No Steam Workshop mod IDs found in {preset_path}")
    log.info(f"Loaded {len(ids)} mod(s) from preset: {preset_path.name}")
    return ids

# ── Steam API ─────────────────────────────────────────────────────────────────
STEAM_API = "https://api.steampowered.com/ISteamRemoteStorage/GetPublishedFileDetails/v1/"

def fetch_mod_details(mod_ids: list[str]) -> list[dict]:
    """Batch-fetch Workshop file details for a list of mod IDs."""
    payload = {"itemcount": len(mod_ids)}
    for i, mid in enumerate(mod_ids):
        payload[f"publishedfileids[{i}]"] = mid

    try:
        r = requests.post(STEAM_API, data=payload, timeout=15)
        r.raise_for_status()
        return r.json()["response"]["publishedfiledetails"]
    except Exception as e:
        log.error(f"Steam API error: {e}")
        return []

# ── Discord ───────────────────────────────────────────────────────────────────
def post_discord_alert(webhook_url: str, mod: dict, old_ts: int, new_ts: int):
    """Send a rich embed to Discord for a mod update."""
    mod_id  = mod["publishedfileid"]
    title   = mod.get("title", f"Mod {mod_id}")
    url     = f"https://steamcommunity.com/sharedfiles/filedetails/?id={mod_id}"
    preview = mod.get("preview_url", "")
    old_dt  = datetime.fromtimestamp(old_ts, tz=timezone.utc).strftime("%d %b %Y %H:%M UTC") if old_ts else "Unknown"
    new_dt  = datetime.fromtimestamp(new_ts, tz=timezone.utc).strftime("%d %b %Y %H:%M UTC")

    embed = {
        "title": f"🔔 Mod Updated: {title}",
        "url": url,
        "color": 0xF4A300,
        "fields": [
            {"name": "Previous update", "value": old_dt, "inline": True},
            {"name": "New update",      "value": new_dt, "inline": True},
            {"name": "Workshop page",   "value": f"[Open on Steam]({url})", "inline": False},
        ],
        "footer": {"text": "44th Arma Mod Watcher"},
        "timestamp": datetime.now(tz=timezone.utc).isoformat(),
    }
    if preview:
        embed["thumbnail"] = {"url": preview}

    try:
        r = requests.post(webhook_url, json={"embeds": [embed]}, timeout=10)
        r.raise_for_status()
        log.info(f"  → Discord alerted for '{title}'")
    except Exception as e:
        log.error(f"Discord webhook error: {e}")

# ── State helpers ─────────────────────────────────────────────────────────────
def load_state() -> dict:
    if STATE_FILE.exists():
        return json.loads(STATE_FILE.read_text())
    return {}

def save_state(state: dict):
    STATE_FILE.write_text(json.dumps(state, indent=2))

# ── Core poll ─────────────────────────────────────────────────────────────────
def poll(config: dict):
    webhook_url = os.environ.get("DISCORD_WEBHOOK_URL") or config.get("discord_webhook_url", "")
    if not webhook_url:
        log.error("No Discord webhook URL set. Add DISCORD_WEBHOOK_URL env var or set it in config.json.")
        return

    preset_path = Path(__file__).parent / config["preset_file"]
    mod_ids = load_preset(preset_path)
    state   = load_state()

    log.info(f"Polling {len(mod_ids)} mod(s)…")
    details = fetch_mod_details(mod_ids)

    updated_count = 0
    for mod in details:
        mid    = mod["publishedfileid"]
        new_ts = int(mod.get("time_updated", 0))
        old_ts = state.get(mid, 0)
        title  = mod.get("title", mid)

        if new_ts > old_ts:
            if old_ts == 0:
                log.info(f"  First seen: '{title}' — storing baseline, no alert sent")
            else:
                log.info(f"  UPDATED: '{title}' — {old_ts} → {new_ts}")
                post_discord_alert(webhook_url, mod, old_ts, new_ts)
                updated_count += 1
            state[mid] = new_ts
        else:
            log.info(f"  No change: '{title}'")

    save_state(state)
    if updated_count == 0:
        log.info("No updates found this cycle.")

# ── Entry point ───────────────────────────────────────────────────────────────
def main():
    once = "--once" in sys.argv

    if not CONFIG_FILE.exists():
        log.error("config.json not found — copy config.example.json and fill it in.")
        raise SystemExit(1)

    config = json.loads(CONFIG_FILE.read_text())

    if once:
        log.info("Arma Mod Watcher — single poll mode (GitHub Actions)")
        poll(config)
    else:
        interval = config.get("poll_interval_minutes", 60) * 60
        log.info(f"Arma Mod Watcher started — polling every {config.get('poll_interval_minutes', 60)} minutes")
        while True:
            try:
                poll(config)
            except Exception as e:
                log.error(f"Unexpected error during poll: {e}")
            log.info(f"Sleeping {interval}s until next poll…\n")
            time.sleep(interval)

if __name__ == "__main__":
    main()
