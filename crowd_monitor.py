"""
Hog's Breath Saloon → Discord crowd monitor
Grabs a frame from the YouTube livestream, counts people using YOLOv8,
and sends a Discord notification when the bar gets busy (10+ people).
Runs as a GitHub Actions scheduled workflow every 15 minutes during bar hours.
"""

import json
import os
import subprocess
import sys
from datetime import datetime
from zoneinfo import ZoneInfo

import requests
from ultralytics import YOLO

YOUTUBE_URL = "https://www.youtube.com/watch?v=YWs0HMRVCBY"
WEBHOOK_URL = os.environ.get("CROWD_DISCORD_WEBHOOK", "")
FRAME_PATH = "/tmp/bar_frame.jpg"
STATE_FILE = "crowd_state.json"
THRESHOLD = 10
TIMEZONE = ZoneInfo("America/New_York")

# Bar hours: 12 PM - 1 AM ET
BAR_OPEN_HOUR = 12
BAR_CLOSE_HOUR = 1


def is_bar_hours():
    """Check if it's currently bar hours in ET."""
    now = datetime.now(TIMEZONE)
    hour = now.hour
    # 12 PM (12) through midnight (23) and midnight through 1 AM (0-1)
    return hour >= BAR_OPEN_HOUR or hour < BAR_CLOSE_HOUR


def grab_frame():
    """Grab a single frame from the YouTube livestream."""
    clip_path = "/tmp/bar_clip.ts"

    # Remove old files
    for f in [FRAME_PATH, clip_path]:
        if os.path.exists(f):
            os.remove(f)

    # Download a ~2 second clip using yt-dlp (handles auth/signing internally)
    result = subprocess.run(
        [
            "yt-dlp", "-f", "best[height<=480]",
            "--js-runtimes", "nodejs,deno",
            "--downloader", "ffmpeg",
            "--downloader-args", "ffmpeg:-t 3",
            "-o", clip_path,
            "--no-part",
            YOUTUBE_URL,
        ],
        capture_output=True, text=True, timeout=60,
    )
    if not os.path.exists(clip_path) or os.path.getsize(clip_path) == 0:
        raise RuntimeError(f"Could not download clip: {result.stderr}")

    print(f"[OK] Downloaded clip: {os.path.getsize(clip_path)} bytes")

    # Extract first frame from the downloaded clip
    subprocess.run(
        [
            "ffmpeg", "-i", clip_path,
            "-frames:v", "1", "-y", "-loglevel", "error",
            FRAME_PATH,
        ],
        capture_output=True, timeout=15,
    )

    # Clean up clip
    if os.path.exists(clip_path):
        os.remove(clip_path)

    if not os.path.exists(FRAME_PATH):
        raise RuntimeError("Failed to extract frame from clip")

    print(f"[OK] Captured frame: {os.path.getsize(FRAME_PATH)} bytes")


def count_people():
    """Run YOLOv8 nano on the frame and count people."""
    model = YOLO("yolov8n.pt")
    results = model(FRAME_PATH, verbose=False)

    people = 0
    for result in results:
        for box in result.boxes:
            if int(box.cls) == 0:  # class 0 = person in COCO
                people += 1

    return people


def load_state():
    if os.path.exists(STATE_FILE):
        with open(STATE_FILE) as f:
            return json.load(f)
    return {"is_busy": False}


def save_state(state):
    with open(STATE_FILE, "w") as f:
        json.dump(state, f, indent=2)


def send_discord(count, busy=True):
    """Send a Discord notification about crowd level."""
    if busy:
        title = "🍺 Hog's Breath is Busy!"
        description = (
            f"**~{count} people** spotted on camera\n\n"
            f"📺 Watch: {YOUTUBE_URL}"
        )
        color = 16749568  # orange
    else:
        title = "🍺 Hog's Breath Has Quieted Down"
        description = (
            f"**~{count} people** on camera now\n\n"
            f"📺 Watch: {YOUTUBE_URL}"
        )
        color = 3066993  # green

    payload = {
        "embeds": [
            {
                "title": title,
                "description": description,
                "color": color,
                "footer": {"text": "Hog's Breath Saloon • Crowd Monitor"},
            }
        ]
    }

    resp = requests.post(WEBHOOK_URL, json=payload, timeout=10)
    resp.raise_for_status()


def main():
    if not WEBHOOK_URL:
        print("[ERROR] No CROWD_DISCORD_WEBHOOK set")
        return

    now = datetime.now(TIMEZONE)
    print(f"[CHECK] {now.strftime('%Y-%m-%d %I:%M %p %Z')}")

    if not is_bar_hours() and "--test" not in sys.argv:
        print("[OK] Outside bar hours, skipping")
        return

    try:
        grab_frame()
    except Exception as e:
        print(f"[ERROR] Could not grab frame: {e}")
        print("[WARN] Stream may be offline")
        return

    count = count_people()
    print(f"[OK] Detected {count} people")

    state = load_state()
    was_busy = state.get("is_busy", False)
    is_busy = count >= THRESHOLD

    if "--test" in sys.argv:
        print("[TEST] Sending test notification")
        send_discord(count, busy=is_busy)
        print(f"[OK] Test notification sent ({count} people)")
        return

    if is_busy and not was_busy:
        print(f"[ALERT] Bar just got busy! ({count} people)")
        send_discord(count, busy=True)
        state["is_busy"] = True
    elif not is_busy and was_busy:
        print(f"[OK] Bar has quieted down ({count} people)")
        send_discord(count, busy=False)
        state["is_busy"] = False
    elif is_busy:
        print(f"[OK] Still busy ({count} people), already notified")
    else:
        print(f"[OK] Not busy ({count} people)")

    save_state(state)


if __name__ == "__main__":
    main()
