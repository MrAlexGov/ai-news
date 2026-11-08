from __future__ import annotations

import json
import os
from pathlib import Path

import requests

token = os.environ["TELEGRAM_BOT_TOKEN"]
chat_id = os.getenv("TELEGRAM_CHAT_ID", "").strip() or os.getenv("TELEGRAM_CHAT_ID_VARIABLE", "").strip()
if not chat_id:
    raise RuntimeError("TELEGRAM_CHAT_ID is missing.")

image_path = Path("data/digest.png")
if not image_path.is_file():
    raise FileNotFoundError("data/digest.png was not found.")

caption = "🎨 Карикатура дня"
digest_path = Path("data/digest.json")
if digest_path.is_file():
    digest = json.loads(digest_path.read_text(encoding="utf-8"))
    caption = str(digest.get("image_caption") or caption)

with image_path.open("rb") as photo:
    response = requests.post(
        f"https://api.telegram.org/bot{token}/sendPhoto",
        data={"chat_id": chat_id, "caption": caption},
        files={"photo": ("digest.png", photo, "image/png")},
        timeout=120,
    )
response.raise_for_status()
payload = response.json()
if not payload.get("ok"):
    raise RuntimeError(f"Telegram sendPhoto failed: {payload}")
print("Editorial image sent to Telegram.")
