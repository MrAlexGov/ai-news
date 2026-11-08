from __future__ import annotations

import html
import json
import os
import re
import sys
from pathlib import Path
from typing import Any

import requests

BOT_TOKEN = os.environ["TELEGRAM_BOT_TOKEN"]
CHAT_ID = (
    os.getenv("TELEGRAM_CHAT_ID", "").strip()
    or os.getenv("TELEGRAM_CHAT_ID_VARIABLE", "").strip()
)
GITHUB_TOKEN = os.getenv("GITHUB_TOKEN", "").strip()
GITHUB_REPOSITORY = os.getenv("GITHUB_REPOSITORY", "").strip()
DIGEST_PATH = Path(os.getenv("DIGEST_PATH", "data/digest.json"))
SESSION = requests.Session()
SESSION.headers.update({"User-Agent": "AI-News-Telegram-Publisher/2.0"})


def esc(value: Any) -> str:
    return html.escape(str(value or ""), quote=True)


def load_digest() -> dict[str, Any]:
    if not DIGEST_PATH.is_file():
        raise FileNotFoundError(
            f"{DIGEST_PATH} not found. The AI editor must commit data/digest.json first."
        )
    with DIGEST_PATH.open("r", encoding="utf-8") as file:
        digest = json.load(file)
    if not isinstance(digest, dict) or not isinstance(digest.get("news"), list):
        raise ValueError("Invalid digest schema: expected an object with a news array.")
    if not digest["news"]:
        raise ValueError("Digest contains no verified news items; refusing to publish an empty issue.")
    return digest


def format_digest(digest: dict[str, Any]) -> str:
    date = esc(digest.get("date", ""))
    window = esc(digest.get("window", "Последние 24 часа"))
    parts = [
        f"🧠 <b>AI НОВОСТИ — {date}</b>",
        f"<i>{window}</i>",
    ]

    for index, item in enumerate(digest["news"], 1):
        if not isinstance(item, dict):
            continue
        title = esc(item.get("title", "Без заголовка"))
        body = esc(item.get("text", item.get("summary", "")))
        block = [f"<b>{index}. {title}</b>"]
        if body:
            block.append(body)

        monetization = item.get("monetization_comment")
        if monetization:
            block.append("💼 " + esc(monetization))

        access = item.get("access")
        if access:
            block.append("ℹ️ " + esc(access))

        free_access = item.get("free_access")
        if free_access:
            block.append("🆓 " + esc(free_access))

        popularity = item.get("popularity")
        if popularity:
            block.append("📈 " + esc(popularity))

        discussion = item.get("discussion")
        if discussion:
            block.append("💬 " + esc(discussion))

        links = item.get("links")
        if isinstance(links, list):
            for link in links:
                if isinstance(link, dict) and link.get("url"):
                    label = esc(link.get("label", "Источник"))
                    url = esc(link["url"])
                    block.append(f'🔗 <a href="{url}">{label}</a>')
        elif item.get("url"):
            label = esc(item.get("source", "Первоисточник"))
            block.append(f'🔗 <a href="{esc(item["url"])}">{label}</a>')

        parts.append("\n".join(block))

    openrouter = digest.get("openrouter")
    if isinstance(openrouter, dict):
        ranking_block = ["📊 <b>OpenRouter — заметные изменения</b>"]
        period = openrouter.get("period")
        if period:
            ranking_block.append("<i>" + esc(period) + "</i>")
        for change in openrouter.get("changes", []):
            ranking_block.append("• " + esc(change))
        methodology = openrouter.get("methodology")
        if methodology:
            ranking_block.append("Методология: " + esc(methodology))
        for link in openrouter.get("links", []):
            if isinstance(link, dict) and link.get("url"):
                label = esc(link.get("label", "Рейтинг"))
                url = esc(link["url"])
                ranking_block.append(f'🔗 <a href="{url}">{label}</a>')
        parts.append("\n".join(ranking_block))

    takeaways = digest.get("takeaways") or digest.get("conclusions")
    if isinstance(takeaways, list) and takeaways:
        parts.append("<b>Главное за день</b>\n" + "\n".join("• " + esc(x) for x in takeaways))
    elif isinstance(takeaways, str) and takeaways.strip():
        parts.append("<b>Главное за день</b>\n" + esc(takeaways))

    return "\n\n".join(parts)


def persist_chat_id_as_repository_variable(chat_id: str) -> None:
    if not GITHUB_TOKEN or not GITHUB_REPOSITORY:
        print("GitHub token/repository unavailable; chat ID was not persisted.")
        return
    api = f"https://api.github.com/repos/{GITHUB_REPOSITORY}/actions/variables/TELEGRAM_CHAT_ID"
    headers = {
        "Authorization": f"Bearer {GITHUB_TOKEN}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    }
    try:
        current = SESSION.get(api, headers=headers, timeout=20)
        if current.status_code == 404:
            response = SESSION.post(
                f"https://api.github.com/repos/{GITHUB_REPOSITORY}/actions/variables",
                headers=headers,
                json={"name": "TELEGRAM_CHAT_ID", "value": chat_id},
                timeout=20,
            )
        else:
            current.raise_for_status()
            response = SESSION.patch(
                api, headers=headers,
                json={"name": "TELEGRAM_CHAT_ID", "value": chat_id}, timeout=20
            )
        response.raise_for_status()
        print("Telegram chat ID saved to GitHub Actions Variables.")
    except requests.RequestException as exc:
        status = getattr(getattr(exc, "response", None), "status_code", "unknown")
        print(f"Could not persist Telegram chat ID (HTTP {status}): {exc}", file=sys.stderr)


def resolve_chat_id() -> str:
    if CHAT_ID:
        return CHAT_ID
    response = SESSION.get(
        f"https://api.telegram.org/bot{BOT_TOKEN}/getUpdates",
        params={"limit": 100, "allowed_updates": json.dumps(["message"])},
        timeout=20,
    )
    response.raise_for_status()
    payload = response.json()
    if not payload.get("ok"):
        raise RuntimeError("Telegram getUpdates failed; check the bot token.")
    private_chats = []
    for update in payload.get("result", []):
        message = update.get("message") or update.get("edited_message") or {}
        chat = message.get("chat") or {}
        if chat.get("type") == "private" and chat.get("id") is not None:
            private_chats.append(chat["id"])
    if not private_chats:
        raise RuntimeError(
            "No private chat found. Open the bot in Telegram, press Start, send it a message, then rerun."
        )
    discovered = str(private_chats[-1])
    persist_chat_id_as_repository_variable(discovered)
    print("Automatically discovered the private Telegram chat ID.")
    return discovered


def split_message(message: str, limit: int = 3900) -> list[str]:
    chunks: list[str] = []
    current = ""
    for block in message.split("\n\n"):
        candidate = (current + "\n\n" + block).strip()
        if len(candidate) > limit and current:
            chunks.append(current)
            current = block
        else:
            current = candidate
    if current:
        chunks.append(current)
    return chunks


def send_telegram(message: str) -> None:
    chat_id = resolve_chat_id()
    for chunk in split_message(message):
        response = SESSION.post(
            f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage",
            json={
                "chat_id": chat_id,
                "text": chunk,
                "parse_mode": "HTML",
                "disable_web_page_preview": True,
            },
            timeout=30,
        )
        response.raise_for_status()
        payload = response.json()
        if not payload.get("ok"):
            raise RuntimeError(f"Telegram API error: {payload}")


def main() -> None:
    digest = load_digest()
    message = format_digest(digest)
    send_telegram(message)
    print(f"Published {len(digest['news'])} editor-selected stories to Telegram.")


if __name__ == "__main__":
    main()
