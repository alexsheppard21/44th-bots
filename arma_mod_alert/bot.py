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
CONFIG_FILE  = Path(__file__).parent / "config.json"
STATE_FILE   = Path(__file__).parent / "mod_state.json"
PENDING_FILE = Path(__file__).parent / "pending_deletes.json"

DELETE_AFTER_SECONDS = 7 * 24 * 60 * 60  # 1 week

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
def parse_webhook(webhook_url: str) -> tuple[str, str]:
    """Extract (webhook_id, webhook_token) from a webhook URL."""
    match = re.search(r'/webhooks/(\d+)/([^/?]+)', webhook_url)
    if not match:
        raise ValueError(f"Could not parse webhook URL: {webhook_url}")
    return match.group(1), match.group(2)

def post_discord_alert(webhook_url: str, mod: dict, old_ts: int, new_ts: int, role_id: str = "") -> str | None:
    """Send a rich embed to Discord for a mod update. Returns the message ID."""
    mod_id  = mod["publishedfileid"]
    title   = mod.get("title", f"Mod {mod_id}")
    url     = f"https://steamcommunity.com/sharedfiles/filedetails/?id={mod_id}"
    preview = mod.get("preview_url", "")
    old_dt  = datetime.fromtimestamp(old_ts, tz=timezone.utc).strftime("%d %b %Y %H:%M UTC") if old_ts else "Unknown"
    new_dt  = datetime.fromtimestamp(new_ts, tz=timezone.utc).strftime("%d %b %Y %H:%M UTC")

    fields = [
        {"name": "Previous update", "value": old_dt, "inline": True},
        {"name": "New update",      "value": new_dt, "inline": True},
        {"name": "Workshop page",   "value": f"[Open on Steam]({url})", "inline": False},
    ]

    embed = {
        "title": f"🔔 Mod Updated: {title}",
        "url": url,
        "color": 0xF4A300,
        "fields": fields,
        "footer": {"text": "44th Arma Mod Watcher"},
        "timestamp": datetime.now(tz=timezone.utc).isoformat(),
    }
    if preview:
        embed["thumbnail"] = {"url": preview}

    try:
        payload = {"embeds": [embed]}
        if role_id:
            payload["content"] = f"<@&{role_id}>"

        # ?wait=true makes Discord return the created message so we can grab its ID
        r = requests.post(webhook_url + "?wait=true", json=payload, timeout=10)
        r.raise_for_status()
        message_id = r.json()["id"]
        log.info(f"  → Discord alerted for '{title}' (message {message_id})")
        return message_id
    except Exception as e:
        log.error(f"Discord webhook error: {e}")
        return None

def delete_discord_message(webhook_id: str, webhook_token: str, message_id: str):
    url = f"https://discord.com/api/webhooks/{webhook_id}/{webhook_token}/messages/{message_id}"
    try:
        r = requests.delete(url, timeout=10)
        if r.status_code == 404:
            log.info(f"  Message {message_id} already deleted")
        else:
            r.raise_for_status()
            log.info(f"  Deleted message {message_id}")
    except Exception as e:
        log.error(f"Discord delete error for message {message_id}: {e}")

# ── Pending deletes ───────────────────────────────────────────────────────────
def load_pending() -> dict:
    if PENDING_FILE.exists():
        return json.loads(PENDING_FILE.read_text())
    return {}

def save_pending(pending: dict):
    PENDING_FILE.write_text(json.dumps(pending, indent=2))

def process_pending_deletes(webhook_url: str):
    pending = load_pending()
    if not pending:
        return

    try:
        webhook_id, webhook_token = parse_webhook(webhook_url)
    except ValueError as e:
        log.error(e)
        return

    now = int(datetime.now(tz=timezone.utc).timestamp())
    due = [msg_id for msg_id, delete_at in pending.items() if now >= delete_at]

    for msg_id in due:
        delete_discord_message(webhook_id, webhook_token, msg_id)
        del pending[msg_id]

    if due:
        save_pending(pending)

# ── State helpers ─────────────────────────────────────────────────────────────
def load_state() -> dict:
    if STATE_FILE.exists():
        return json.loads(STATE_FILE.read_text())
    return {}

def save_state(state: dict):
    STATE_FILE.write_text(json.dumps(state, indent=2))

# ── Core poll ─────────────────────────────────────────────────────────────────
def poll(config: dict):
    webhook_url = os.environ.get("DISCORD_WEBHOOK_URL", "")
    role_id     = config.get("alert_role_id", "")

    if not webhook_url:
        log.error("No Discord webhook URL set. Add DISCORD_WEBHOOK_URL env var or set it in config.json.")
        return

    process_pending_deletes(webhook_url)

    preset_path = Path(__file__).parent / config["preset_file"]
    mod_ids = load_preset(preset_path)
    state   = load_state()
    pending = load_pending()

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
                msg_id = post_discord_alert(webhook_url, mod, old_ts, new_ts, role_id)
                if msg_id:
                    delete_at = int(datetime.now(tz=timezone.utc).timestamp()) + DELETE_AFTER_SECONDS
                    pending[msg_id] = delete_at
                updated_count += 1
            state[mid] = new_ts
        else:
            log.info(f"  No change: '{title}'")

    save_state(state)
    save_pending(pending)
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
