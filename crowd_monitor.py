"""
Elbo Room → Discord crowd monitor
Screenshots the YouTube livestream via Playwright, counts people using YOLOv8,
detects gender ratio with CLIP (full-body cues), and sends tiered Discord notifications.
Runs locally via launchd (macOS) every 15 minutes during bar hours.
"""

import json
import os
import sys
from datetime import datetime
from zoneinfo import ZoneInfo

import cv2
import requests
import torch
from PIL import Image
from playwright.sync_api import sync_playwright
from transformers import CLIPModel, CLIPProcessor
from ultralytics import YOLO

YOUTUBE_URL = "https://www.youtube.com/watch?v=YWs0HMRVCBY"
WEBHOOK_URL = os.environ.get("CROWD_DISCORD_WEBHOOK", "")
FRAME_PATH = "/tmp/bar_frame.png"
STATE_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "crowd_state.json")
TIMEZONE = ZoneInfo("America/New_York")

# Bar hours: 12 PM - 1 AM ET
BAR_OPEN_HOUR = 12
BAR_CLOSE_HOUR = 1

# Crowd tiers
TIERS = [
    (21, "busy"),       # 21+ people
    (11, "little_busy"),  # 11-20 people
    (1, "slow"),        # 1-10 people
    (0, "empty"),       # 0 people
]


def get_tier(count):
    for threshold, tier in TIERS:
        if count >= threshold:
            return tier
    return "empty"


def tier_message(tier, count, men, women):
    gender_line = f"\n👫 **Ratio:** ~{men} men / ~{women} women"

    if tier == "busy":
        return (
            "🍺 Elbo Room is Busy!",
            f"**~{count} people** on camera{gender_line}\n\n📺 Watch: {YOUTUBE_URL}",
            16749568,  # orange
        )
    elif tier == "little_busy":
        return (
            "🍺 Elbo Room is a Little Busy",
            f"**~{count} people** on camera{gender_line}\n\n📺 Watch: {YOUTUBE_URL}",
            16776960,  # yellow
        )
    elif tier == "slow":
        return (
            "🍺 Elbo Room is Slow",
            f"**~{count} people** on camera{gender_line}\n\n📺 Watch: {YOUTUBE_URL}",
            3066993,  # green
        )
    return None


def is_bar_hours():
    now = datetime.now(TIMEZONE)
    hour = now.hour
    return hour >= BAR_OPEN_HOUR or hour < BAR_CLOSE_HOUR


def grab_frame():
    """Screenshot the YouTube livestream video player."""
    if os.path.exists(FRAME_PATH):
        os.remove(FRAME_PATH)

    with sync_playwright() as p:
        browser = p.chromium.launch(
            headless=False,
            channel="chrome",
            args=[
                "--autoplay-policy=no-user-gesture-required",
                "--mute-audio",
            ],
        )
        page = browser.new_page(viewport={"width": 1280, "height": 720})
        page.goto(YOUTUBE_URL, wait_until="domcontentloaded", timeout=30000)
        page.wait_for_timeout(8000)

        player = page.query_selector("#movie_player")
        if player:
            player.screenshot(path=FRAME_PATH)
        else:
            page.screenshot(path=FRAME_PATH)

        browser.close()

    if not os.path.exists(FRAME_PATH) or os.path.getsize(FRAME_PATH) == 0:
        raise RuntimeError("Failed to capture screenshot")

    print(f"[OK] Captured frame: {os.path.getsize(FRAME_PATH)} bytes")


def count_and_analyze():
    """Run YOLOv8 to count people, then CLIP for gender on each full-body crop."""
    yolo = YOLO("yolov8n.pt")
    results = yolo(FRAME_PATH, verbose=False)
    img = cv2.imread(FRAME_PATH)

    people_boxes = []
    for result in results:
        for box in result.boxes:
            if int(box.cls) == 0:  # person
                people_boxes.append(box.xyxy[0].cpu().numpy())

    count = len(people_boxes)
    if count == 0:
        return 0, 0, 0

    # Load CLIP model for full-body gender classification
    clip_model = CLIPModel.from_pretrained("openai/clip-vit-base-patch32")
    clip_processor = CLIPProcessor.from_pretrained("openai/clip-vit-base-patch32")
    labels = ["a man", "a woman"]

    men = 0
    women = 0

    for box in people_boxes:
        x1, y1, x2, y2 = map(int, box)
        h, w = img.shape[:2]
        x1 = max(0, x1 - 5)
        y1 = max(0, y1 - 5)
        x2 = min(w, x2 + 5)
        y2 = min(h, y2 + 5)
        crop = img[y1:y2, x1:x2]

        if crop.size == 0:
            continue

        try:
            # Convert BGR (OpenCV) to RGB PIL image
            crop_rgb = cv2.cvtColor(crop, cv2.COLOR_BGR2RGB)
            crop_pil = Image.fromarray(crop_rgb)

            inputs = clip_processor(
                text=labels, images=crop_pil, return_tensors="pt", padding=True
            )
            with torch.no_grad():
                outputs = clip_model(**inputs)
            probs = outputs.logits_per_image.softmax(dim=1)[0]

            if probs[0] > probs[1]:
                men += 1
            else:
                women += 1
        except Exception:
            men += 1  # Default to man if classification fails

    return count, men, women


def load_state():
    if os.path.exists(STATE_FILE):
        with open(STATE_FILE) as f:
            return json.load(f)
    return {"tier": "empty"}


def save_state(state):
    with open(STATE_FILE, "w") as f:
        json.dump(state, f, indent=2)


def send_discord(title, description, color):
    payload = {
        "embeds": [
            {
                "title": title,
                "description": description,
                "color": color,
                "footer": {"text": "Elbo Room • Crowd Monitor"},
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

    count, men, women = count_and_analyze()
    tier = get_tier(count)
    print(f"[OK] Detected {count} people ({men} men, {women} women) — tier: {tier}")

    if "--test" in sys.argv:
        msg = tier_message(tier, count, men, women)
        if msg:
            title, desc, color = msg
            print(f"[TEST] Sending: {title}")
            send_discord(title, desc, color)
            print(f"[OK] Test notification sent")
        else:
            print("[OK] Bar is empty, no notification")
        return

    state = load_state()
    prev_tier = state.get("tier", "empty")

    if tier != prev_tier and tier != "empty":
        msg = tier_message(tier, count, men, women)
        if msg:
            title, desc, color = msg
            print(f"[ALERT] Tier changed: {prev_tier} → {tier}")
            send_discord(title, desc, color)

    state["tier"] = tier
    save_state(state)


if __name__ == "__main__":
    main()
