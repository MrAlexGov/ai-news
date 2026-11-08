from __future__ import annotations

import base64
import binascii
import html
import json
import os
import re
import subprocess
import sys
import tempfile
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
    parts = [f"🧠 <b>AI НОВОСТИ — {date}</b>"]

    takeaways = digest.get("takeaways") or digest.get("conclusions")
    if isinstance(takeaways, list) and takeaways:
        parts.append("<b>Главное за день</b>\n" + "\n".join("• " + esc(x) for x in takeaways))
    elif isinstance(takeaways, str) and takeaways.strip():
        parts.append("<b>Главное за день</b>\n" + esc(takeaways))

    parts.append("<b>Топ событий за сутки</b>")

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


def get_media_path(
    digest: dict[str, Any],
    file_key: str,
    url_key: str,
    suffix: str,
    max_bytes: int,
) -> tuple[Path | None, bool]:
    local_value = digest.get(file_key)
    if local_value:
        path = Path(str(local_value))
        if not path.is_file():
            raise FileNotFoundError(f"{file_key} specified in digest but not found: {path}")
        return path, False

    media_url = digest.get(url_key)
    if not media_url:
        return None, False
    if not isinstance(media_url, str) or not media_url.startswith("https://"):
        raise ValueError(f"{url_key} must be an HTTPS URL.")

    response = SESSION.get(media_url, stream=True, timeout=(20, 120))
    response.raise_for_status()
    temp = tempfile.NamedTemporaryFile(prefix="digest-", suffix=suffix, delete=False)
    total = 0
    try:
        for chunk in response.iter_content(chunk_size=1024 * 256):
            if not chunk:
                continue
            total += len(chunk)
            if total > max_bytes:
                raise ValueError(f"{url_key} exceeds the {max_bytes}-byte size limit.")
            temp.write(chunk)
        temp.close()
        if total == 0:
            raise ValueError(f"{url_key} returned an empty file.")
        return Path(temp.name), True
    except Exception:
        temp.close()
        Path(temp.name).unlink(missing_ok=True)
        raise
    finally:
        response.close()


def generate_audio_file(digest: dict[str, Any]) -> Path | None:
    script = digest.get("audio_script")
    if not isinstance(script, str) or not script.strip():
        return None
    if len(script) > 5000:
        print("audio_script exceeds 5000 characters; publishing without audio.", file=sys.stderr)
        return None

    wav_file = tempfile.NamedTemporaryFile(prefix="digest-", suffix=".wav", delete=False)
    mp3_file = tempfile.NamedTemporaryFile(prefix="digest-", suffix=".mp3", delete=False)
    wav_path, mp3_path = Path(wav_file.name), Path(mp3_file.name)
    wav_file.close()
    mp3_file.close()

    voice = os.getenv("PIPER_VOICE", "ru_RU-denis-medium")
    data_dir = os.getenv("PIPER_DATA_DIR", ".piper-voices")
    try:
        subprocess.run(
            [
                sys.executable, "-m", "piper", "-m", voice,
                "--data-dir", data_dir, "-f", str(wav_path), "--", script.strip()
            ],
            check=True, capture_output=True, text=True, timeout=600,
        )
        subprocess.run(
            [
                "ffmpeg", "-y", "-loglevel", "error", "-i", str(wav_path),
                "-codec:a", "libmp3lame", "-b:a", "128k", str(mp3_path)
            ],
            check=True, capture_output=True, text=True, timeout=120,
        )
        if not mp3_path.is_file() or mp3_path.stat().st_size == 0:
            raise RuntimeError("Piper/FFmpeg produced an empty MP3.")
        return mp3_path
    except Exception as exc:
        print(f"Local Piper TTS failed; publishing without audio: {exc}", file=sys.stderr)
        mp3_path.unlink(missing_ok=True)
        return None
    finally:
        wav_path.unlink(missing_ok=True)


def send_audio_file(digest: dict[str, Any], generated_path: Path | None = None) -> None:
    if generated_path is not None:
        path, temporary = generated_path, True
    else:
        path, temporary = get_media_path(
            digest, "audio_file", "audio_url", ".mp3", 50 * 1024 * 1024
        )
    if path is None:
        print("No audio_script/audio_file/audio_url; publishing without audio.")
        return
    try:
        chat_id = resolve_chat_id()
        caption = esc(digest.get("audio_caption", "🎧 Аудиоверсия AI-дайджеста"))
        with path.open("rb") as audio:
            response = SESSION.post(
                f"https://api.telegram.org/bot{BOT_TOKEN}/sendAudio",
                data={"chat_id": chat_id, "caption": caption, "parse_mode": "HTML"},
                files={"audio": (path.name, audio, "audio/mpeg")},
                timeout=120,
            )
        response.raise_for_status()
        payload = response.json()
        if not payload.get("ok"):
            raise RuntimeError(f"Telegram audio API error: {payload}")
    finally:
        if temporary:
            path.unlink(missing_ok=True)


def send_editorial_image(digest: dict[str, Any]) -> None:
    path, temporary = get_media_path(
        digest, "image_file", "image_url", ".png", 10 * 1024 * 1024
    )

    # The editor may embed the generated PNG directly in digest.json as base64.
    # This avoids needing a separate binary upload step to GitHub.
    if path is None and digest.get("image_base64"):
        encoded = digest["image_base64"]
        if not isinstance(encoded, str):
            print("image_base64 must be a string; publishing without illustration.", file=sys.stderr)
            return
        if encoded.startswith("data:"):
            encoded = encoded.split(",", 1)[-1]
        if len(encoded) > 14 * 1024 * 1024:
            print("image_base64 is oversized; publishing without illustration.", file=sys.stderr)
            return
        try:
            image_bytes = base64.b64decode(encoded, validate=True)
            if not image_bytes or len(image_bytes) > 10 * 1024 * 1024:
                raise ValueError("decoded PNG is empty or exceeds 10 MB")
            if not image_bytes.startswith(b"\\x89PNG\\r\\n\\x1a\\n"):
                raise ValueError("decoded image is not a PNG file")
            temp = tempfile.NamedTemporaryFile(prefix="digest-", suffix=".png", delete=False)
            temp.write(image_bytes)
            temp.close()
            path = Path(temp.name)
            temporary = True
        except (binascii.Error, ValueError, OSError) as exc:
            print(f"Invalid image_base64; publishing without illustration: {exc}", file=sys.stderr)
            return

    if path is None:
        print("No image_file/image_url/image_base64 specified; publishing without illustration.")
        return
    try:
        chat_id = resolve_chat_id()
        caption = esc(digest.get("image_caption", "🎨 Карикатура дня"))
        with path.open("rb") as photo:
            response = SESSION.post(
                f"https://api.telegram.org/bot{BOT_TOKEN}/sendPhoto",
                data={"chat_id": chat_id, "caption": caption, "parse_mode": "HTML"},
                files={"photo": (path.name, photo, "image/png")},
                timeout=120,
            )
        response.raise_for_status()
        payload = response.json()
        if not payload.get("ok"):
            raise RuntimeError(f"Telegram image API error: {payload}")
    finally:
        if temporary:
            path.unlink(missing_ok=True)

def main() -> None:
    digest = load_digest()
    message = format_digest(digest)
    send_telegram(message)
    send_editorial_image(digest)
    generated_audio = generate_audio_file(digest)
    send_audio_file(digest, generated_path=generated_audio)
    print(f"Published {len(digest['news'])} editor-selected stories to Telegram.")


if __name__ == "__main__":
    main()
