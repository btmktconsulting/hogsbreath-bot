import json
import os
import re
import time
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import requests
from bs4 import BeautifulSoup

URL = "https://www.hogsbreath.com/events/"
WEBHOOK_URL = os.environ.get("DISCORD_WEBHOOK_URL", "https://discord.com/api/webhooks/1484607609024811020/oOXPvAMe07M1CKg2vTwbFhqIbEzrirXp-IIOf2FhQ5ZDHhMrhj3QvNIi3dxQWKQ5Zp8p")
STATE_FILE = "/tmp/hogs_state.json"
TIMEZONE = "America/New_York"
CHECK_INTERVAL_SECONDS = 60

HEADERS = {
    "User-Agent": "Mozilla/5.0"
}

MONTHS = {
    "Jan": 1, "Feb": 2, "Mar": 3, "Apr": 4, "May": 5, "Jun": 6,
    "Jul": 7, "Aug": 8, "Sep": 9, "Oct": 10, "Nov": 11, "Dec": 12
}

EVENT_RE = re.compile(
    r'(?P<mon>[A-Z][a-z]{2})\s+'
    r'(?P<day>\d{2})\s+'
    r'(?P<artist>.*?)\s+'
    r'(?:[A-Za-z]+\s+\d{1,2})\s+@\s+\(\s*'
    r'(?P<start>\d{1,2}:\d{2}\s*[AP]M)\s*-\s*'
    r'(?P<end>\d{1,2}:\d{2}\s*[AP]M)\s*\)'
)

def load_state():
    if os.path.exists(STATE_FILE):
        with open(STATE_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    return {}

def save_state(state):
    with open(STATE_FILE, "w", encoding="utf-8") as f:
        json.dump(state, f)

def parse_time(date_obj, time_str, tz):
    dt = datetime.strptime(time_str.replace(" ", ""), "%I:%M%p")
    return datetime(
        year=date_obj.year,
        month=date_obj.month,
        day=date_obj.day,
        hour=dt.hour,
        minute=dt.minute,
        tzinfo=tz
    )

def fetch_events():
    r = requests.get(URL, headers=HEADERS, timeout=20)
    r.raise_for_status()
    soup = BeautifulSoup(r.text, "html.parser")

    tz = ZoneInfo(TIMEZONE)
    year = datetime.now(tz).year
    events = []

    link_texts = []
    for a in soup.find_all("a"):
        txt = " ".join(a.get_text(" ", strip=True).split())
        if txt:
            link_texts.append(txt)

    for text in link_texts:
        if "@" not in text or ("AM" not in text and "PM" not in text):
            continue

        m = EVENT_RE.search(text)
        if not m:
            continue

        mon = MONTHS[m.group("mon")]
        day = int(m.group("day"))
        artist = m.group("artist").strip()
        start_str = m.group("start").strip()
        end_str = m.group("end").strip()

        event_date = datetime(year, mon, day, tzinfo=tz)
        start_dt = parse_time(event_date, start_str, tz)
        end_dt = parse_time(event_date, end_str, tz)

        if end_dt <= start_dt:
            end_dt += timedelta(days=1)

        events.append({
            "artist": artist,
            "start": start_dt,
            "end": end_dt,
        })

    return events

def next_act(events, now):
    upcoming = [e for e in events if e["start"] > now]
    if not upcoming:
        return None
    upcoming.sort(key=lambda e: e["start"])
    return upcoming[0]

def event_starting_now(events, now, grace_minutes=2):
    candidates = []
    for e in events:
        delta_minutes = (now - e["start"]).total_seconds() / 60
        if 0 <= delta_minutes < grace_minutes:
            candidates.append(e)
    if not candidates:
        return None
    candidates.sort(key=lambda e: e["start"], reverse=True)
    return candidates[0]

def send_discord_message(content):
    r = requests.post(WEBHOOK_URL, json={"content": content}, timeout=20)
    r.raise_for_status()

def check_and_notify():
    state = load_state()
    tz = ZoneInfo(TIMEZONE)
    now = datetime.now(tz)

    try:
        events = fetch_events()
    except Exception as e:
        print(f"[{now.strftime('%Y-%m-%d %H:%M:%S')}] Error fetching events: {e}")
        return

    act = event_starting_now(events, now, grace_minutes=2)

    if not act:
        return

    current_key = f"{act['artist']}|{act['start'].isoformat()}"
    last_key = state.get("last_start_alert_key")

    if current_key == last_key:
        return

    upcoming = next_act(events, act["start"])

    msg = (
        f"🎵 **Now playing at Hog's Breath:** {act['artist']}\n"
        f"🕒 {act['start'].strftime('%b %d %I:%M %p')} – {act['end'].strftime('%I:%M %p')}"
    )

    if upcoming:
        msg += f"\n➡️ Next: {upcoming['artist']} at {upcoming['start'].strftime('%I:%M %p')}"

    try:
        send_discord_message(msg)
        print(f"[{now.strftime('%Y-%m-%d %H:%M:%S')}] Sent alert: {act['artist']}")
    except Exception as e:
        print(f"[{now.strftime('%Y-%m-%d %H:%M:%S')}] Error sending Discord message: {e}")
        return

    state["last_start_alert_key"] = current_key
    save_state(state)

def main():
    print("Hog's Breath Discord bot started. Checking every 60 seconds...")
    while True:
        try:
            check_and_notify()
        except Exception as e:
            print(f"Unexpected error: {e}")
        time.sleep(CHECK_INTERVAL_SECONDS)

if __name__ == "__main__":
    main()
