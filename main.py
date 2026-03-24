"""
Hog's Breath Saloon → Discord live performer notifier
Scrapes the events page and sends a Discord alert ~1 minute before each performance.
Runs as a GitHub Actions scheduled workflow at show times (12 PM, 4:30 PM, 9 PM ET).
"""

import os
import re
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import requests
from bs4 import BeautifulSoup

URL = "https://www.hogsbreath.com/events/"
WEBHOOK_URL = os.environ.get("DISCORD_WEBHOOK_URL", "")
TIMEZONE = ZoneInfo("America/New_York")

HEADERS = {"User-Agent": "Mozilla/5.0"}

MONTHS = {
    "Jan": 1, "Feb": 2, "Mar": 3, "Apr": 4, "May": 5, "Jun": 6,
    "Jul": 7, "Aug": 8, "Sep": 9, "Oct": 10, "Nov": 11, "Dec": 12,
    "January": 1, "February": 2, "March": 3, "April": 4, "May": 5,
    "June": 6, "July": 7, "August": 8, "September": 9, "October": 10,
    "November": 11, "December": 12,
}


def fetch_todays_events():
    """Scrape the events page and return today's performances."""
    resp = requests.get(URL, headers=HEADERS, timeout=20)
    resp.raise_for_status()
    soup = BeautifulSoup(resp.text, "html.parser")

    now = datetime.now(TIMEZONE)
    today = now.date()
    year = now.year
    events = []

    for a in soup.find_all("a"):
        text = " ".join(a.get_text(" ", strip=True).split())
        if "@" not in text or ("AM" not in text and "PM" not in text):
            continue

        h4 = a.find("h4")
        if not h4:
            continue
        artist = h4.get_text(strip=True)

        # Match time range: "@ ( 12:00 PM - 4:00 PM )" or "@ 12:00 PM - 4:00 PM"
        time_match = re.search(
            r"@\s*\(?\s*(\d{1,2}:\d{2}\s*[AP]M)\s*-\s*(\d{1,2}:\d{2}\s*[AP]M)\s*\)?",
            text,
        )
        if not time_match:
            continue

        # Match date right before the @: "March 24 @" or "Mar 24 @"
        date_match = re.search(r"(\w+)\s+(\d{1,2})\s+@", text)
        if not date_match:
            continue

        month_str = date_match.group(1)
        day = int(date_match.group(2))
        if month_str not in MONTHS:
            continue
        month = MONTHS[month_str]

        event_date = datetime(year, month, day, tzinfo=TIMEZONE).date()
        if event_date != today:
            continue

        start_str = time_match.group(1).strip()
        end_str = time_match.group(2).strip()

        start_time = datetime.strptime(start_str.replace(" ", ""), "%I:%M%p")
        end_time = datetime.strptime(end_str.replace(" ", ""), "%I:%M%p")

        start_dt = datetime(
            year, month, day, start_time.hour, start_time.minute, tzinfo=TIMEZONE
        )
        end_dt = datetime(
            year, month, day, end_time.hour, end_time.minute, tzinfo=TIMEZONE
        )
        if end_dt <= start_dt:
            end_dt += timedelta(days=1)

        events.append({"artist": artist, "start": start_dt, "end": end_dt})

    events.sort(key=lambda e: e["start"])
    return events


def send_discord_notification(event, next_event=None):
    """Send a Discord embed about an upcoming performance."""
    description = (
        f"🕒 {event['start'].strftime('%I:%M %p')} – "
        f"{event['end'].strftime('%I:%M %p')}"
    )
    if next_event:
        description += (
            f"\n\n➡️ **Up next:** {next_event['artist']} "
            f"at {next_event['start'].strftime('%I:%M %p')}"
        )

    payload = {
        "embeds": [
            {
                "title": f"🎵 {event['artist']}",
                "description": description,
                "color": 16749568,
                "footer": {"text": "Hog's Breath Saloon • Live Music"},
                "url": "https://www.hogsbreath.com/events/",
            }
        ]
    }

    resp = requests.post(WEBHOOK_URL, json=payload, timeout=10)
    resp.raise_for_status()
    print(f"[OK] Discord notified: {event['artist']} at {event['start'].strftime('%I:%M %p')}")


def main():
    if not WEBHOOK_URL:
        print("[ERROR] No DISCORD_WEBHOOK_URL set")
        return

    now = datetime.now(TIMEZONE)
    print(f"[CHECK] {now.strftime('%Y-%m-%d %I:%M %p %Z')}")

    events = fetch_todays_events()
    if not events:
        print("[OK] No events found for today")
        return

    print(f"[OK] Found {len(events)} event(s) today:")
    for e in events:
        print(f"     {e['artist']} — {e['start'].strftime('%I:%M %p')}")

    # Find events starting within the next 5 minutes
    for i, event in enumerate(events):
        minutes_until = (event["start"] - now).total_seconds() / 60
        if -1 <= minutes_until <= 5:
            next_event = events[i + 1] if i + 1 < len(events) else None
            send_discord_notification(event, next_event)
            return

    print("[OK] No performances starting soon")


if __name__ == "__main__":
    main()
