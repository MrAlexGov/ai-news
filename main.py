from __future__ import annotations

import html
import json
import os
import re
import sys
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from typing import Any
from urllib.parse import quote_plus

import feedparser
import requests

BOT_TOKEN = os.environ["TELEGRAM_BOT_TOKEN"]
CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "").strip() or os.getenv("TELEGRAM_CHAT_ID_VARIABLE", "").strip()
GITHUB_TOKEN = os.getenv("GITHUB_TOKEN", "").strip()
GITHUB_REPOSITORY = os.getenv("GITHUB_REPOSITORY", "").strip()
WINDOW_HOURS = int(os.getenv("WINDOW_HOURS", "24"))
MAX_ITEMS = int(os.getenv("MAX_ITEMS", "10"))
UTC_NOW = datetime.now(timezone.utc)
CUTOFF = UTC_NOW - timedelta(hours=WINDOW_HOURS)
SESSION = requests.Session()
SESSION.headers.update({"User-Agent": "AI-News-Digest/1.0 (+https://github.com/MrAlexGov/ai-news)"})
TRANSLATION_CACHE: dict[str, str] = {}

FEEDS = [
    ("OpenAI", "https://openai.com/news/rss.xml"),
    ("Anthropic", "https://www.anthropic.com/news/rss.xml"),
    ("Google AI", "https://blog.google/technology/ai/rss/"),
    ("Google DeepMind", "https://deepmind.google/blog/rss.xml"),
    ("NVIDIA", "https://blogs.nvidia.com/feed/"),
    ("Hugging Face", "https://huggingface.co/blog/feed.xml"),
    ("MIT Technology Review", "https://www.technologyreview.com/topic/artificial-intelligence/feed/"),
    ("TechCrunch AI", "https://techcrunch.com/category/artificial-intelligence/feed/"),
    ("The Verge AI", "https://www.theverge.com/rss/ai-artificial-intelligence/index.xml"),
    ("VentureBeat AI", "https://venturebeat.com/category/ai/feed/"),
    ("Ars Technica AI", "https://feeds.arstechnica.com/arstechnica/technology-lab"),
    ("AI News", "https://www.artificialintelligence-news.com/feed/"),
    ("Nous Research", "https://nousresearch.com/feed/"),
]

AI_TERMS = re.compile(r"\b(ai|artificial intelligence|machine learning|llm|language model|agent|生成|ии|нейросет|deep learning|генеративн|робот|openai|anthropic|gemini|claude|gpt|llama|mistral|nvidia|deepmind)\b", re.I)


PRIORITY_TERMS = {
    "free": 9, "free tier": 10, "free access": 10, "free model": 10,
    "open source": 8, "open-source": 8, "open weights": 8, "free api": 10,
    "agent": 7, "assistant": 7, "app": 6, "application": 6,
    "launch": 4, "release": 4, "available": 3, "trending": 5,
    "hermes": 8, "openclaw": 8, "tool": 4, "browser": 4,
    "бесплат": 10, "открыт": 8, "агент": 7, "ассистент": 7, "приложен": 6,
}


def translate_ru(value: str, limit: int = 900) -> str:
    """Translate editorial text to Russian, caching repeated strings."""
    value = clean(value, limit)
    if not value or re.search(r"[А-Яа-яЁё]", value):
        return value
    if value in TRANSLATION_CACHE:
        return TRANSLATION_CACHE[value]
    try:
        response = SESSION.get(
            "https://translate.googleapis.com/translate_a/single",
            params={"client": "gtx", "sl": "auto", "tl": "ru", "dt": "t", "q": value},
            timeout=15,
        )
        response.raise_for_status()
        translated = "".join(part[0] for part in response.json()[0] if part and part[0])
        result = translated.strip() or value
    except Exception as exc:
        print(f"Translation unavailable: {exc}", file=sys.stderr)
        result = value
    TRANSLATION_CACHE[value] = result
    return result


def relevance_score(item: dict[str, Any]) -> int:
    text = (item.get("title", "") + " " + item.get("summary", "")).lower()
    score = sum(weight for term, weight in PRIORITY_TERMS.items() if term in text)
    if re.search(r"\b(model|assistant|agent|app|tool|api|модель|ассистент|агент|приложен)\b", text):
        score += 3
    if re.search(r"\b(raises|funding|valuation|lawsuit|partnership|acquisition)\b", text):
        score -= 3
    return score


def fetch_openrouter_radar() -> str:
    """Summarize public OpenRouter usage rankings and free-model/app discovery pages."""
    try:
        response = SESSION.get("https://openrouter.ai/for-ai", timeout=25)
        response.raise_for_status()
        raw = html.unescape(response.text)
        raw = re.sub(r"<[^>]+>", "\n", raw)
        raw = re.sub(r"[ \t]+", " ", raw)
        lines = [re.sub(r"\s+", " ", line).strip() for line in raw.splitlines()]
        lines = [line for line in lines if line]
        markers = [
            ("Популярные модели", ("Top models this month", "Top models by weekly usage")),
            ("Новые и быстрорастущие модели", ("New and trending models",)),
            ("Популярные модели генерации изображений", ("Top Image models", "Top image models")),
            ("Популярные приложения и агенты", ("Top Apps", "App rankings")),
        ]
        sections = []
        for heading, variants in markers:
            start = next((i for i, line in enumerate(lines) if any(v.lower() in line.lower() for v in variants)), None)
            if start is None:
                continue
            excerpt = []
            for line in lines[start + 1:start + 10]:
                line = line[:217] + "..." if len(line) > 220 else line
                if line not in excerpt:
                    excerpt.append(line)
                if len(excerpt) >= 5:
                    break
            if excerpt:
                sections.append("<b>" + heading + "</b>\n" + "\n".join(html.escape(translate_ru(x, 240)) for x in excerpt))
        if not sections:
            return ""
        return (
            "\n\n📊 <b>РАДАР OPENROUTER</b>\n"
            + "\n\n".join(sections)
            + '\n\n<a href="https://openrouter.ai/rankings">Рейтинг моделей</a> · '
              '<a href="https://openrouter.ai/rankings/image">Рейтинг генерации изображений</a> · '
              '<a href="https://openrouter.ai/apps">Каталог приложений и агентов</a> · '
              '<a href="https://openrouter.ai/collections/free-models">Бесплатные модели</a>'
        )
    except Exception as exc:
        print(f"OpenRouter radar unavailable: {exc}", file=sys.stderr)
        return ""


def clean(value: str, limit: int = 600) -> str:
    value = re.sub(r"<[^>]+>", " ", value or "")
    value = html.unescape(value)
    value = re.sub(r"https?://\S+", "", value)
    value = re.sub(r"\s+", " ", value).strip()
    return value[:limit].rstrip()


def entry_date(entry: Any) -> datetime | None:
    for key in ("published", "updated", "created"):
        raw = entry.get(key)
        if raw:
            try:
                dt = parsedate_to_datetime(raw)
                return dt.replace(tzinfo=timezone.utc) if dt.tzinfo is None else dt.astimezone(timezone.utc)
            except Exception:
                pass
    for key in ("published_parsed", "updated_parsed"):
        value = entry.get(key)
        if value:
            return datetime(*value[:6], tzinfo=timezone.utc)
    return None


def fetch_news() -> list[dict[str, Any]]:
    found: dict[str, dict[str, Any]] = {}
    for source, url in FEEDS:
        try:
            parsed = feedparser.parse(url, request_headers={"User-Agent": "Mozilla/5.0 AI-News-Digest"})
            for e in parsed.entries:
                title = clean(e.get("title", ""), 220)
                link = e.get("link", "")
                dt = entry_date(e)
                if not title or not link or not dt or dt < CUTOFF or dt > UTC_NOW + timedelta(hours=1):
                    continue
                summary = clean(e.get("summary", "") or e.get("description", ""), 900)
                if not AI_TERMS.search(title + " " + summary):
                    continue
                key = re.sub(r"\W+", "", title.lower())
                if key not in found:
                    found[key] = {"title": title, "url": link, "source": source, "date": dt, "summary": summary}
        except Exception as exc:
            print(f"Feed error ({source}): {exc}", file=sys.stderr)
    return sorted(found.values(), key=lambda x: (relevance_score(x), x["date"]), reverse=True)


def hn_reaction(title: str) -> str | None:
    try:
        url = "https://hn.algolia.com/api/v1/search"
        data = SESSION.get(url, params={"query": title[:100], "tags": "story", "numericFilters": "created_at_i>" + str(int(CUTOFF.timestamp()))}, timeout=15).json()
        hits = data.get("hits", [])
        if not hits:
            return None
        hit = max(hits, key=lambda x: x.get("points", 0) + 2 * x.get("num_comments", 0))
        points, comments = hit.get("points", 0), hit.get("num_comments", 0)
        if points == 0 and comments == 0:
            return None
        story_url = hit.get("url") or ("https://news.ycombinator.com/item?id=" + str(hit.get("objectID")))
        comment_texts = []
        kids = hit.get("children", []) or []
        for c in kids[:4]:
            text = clean(c.get("text", ""), 240)
            if text:
                comment_texts.append(text)
        result = f"Hacker News: {points} баллов, {comments} комментариев. [Обсуждение]({story_url})"
        if comment_texts:
            result += "\nПримеры комментариев: " + " / ".join(comment_texts[:2])
        return result
    except Exception as exc:
        print(f"HN lookup error: {exc}", file=sys.stderr)
        return None


def build_digest(items: list[dict[str, Any]]) -> str:
    local_day = UTC_NOW.astimezone(timezone(timedelta(hours=3))).strftime("%d.%m.%Y")
    header = f"🧠 <b>AI НОВОСТИ — {local_day}</b>\nЗа последние {WINDOW_HOURS} часов · бесплатные модели, агенты и AI-приложения\n"
    if not items:
        return header + "\nЗа выбранные 24 часа не удалось подтвердить новости из подключённых источников. Попробуем в следующем выпуске."
    blocks = [header]
    for i, item in enumerate(items[:MAX_ITEMS], 1):
        summary = item["summary"] or "В RSS-ленте опубликована новая новость; подробности — по ссылке на первоисточник."
        summary = translate_ru(summary[:700], 700)
        title_ru = translate_ru(item["title"], 220)
        reaction = hn_reaction(item["title"])
        published = item["date"].strftime("%d.%m %H:%M UTC")
        block = (
            f"\n<b>{i}. {html.escape(title_ru)}</b>\n"
            f"<i>{html.escape(item['source'])} · {published}</i>\n"
            f"{html.escape(summary)}\n"
            f"🔗 <a href=\"{html.escape(item['url'], quote=True)}\">Первоисточник</a>"
        )
        if reaction:
            block += "\n💬 " + html.escape(reaction).replace("&lt;", "<").replace("&gt;", ">")
        else:
            block += "\n💬 Реакция сообщества: заметного обсуждения в Hacker News не найдено."
        blocks.append(block)
    radar = fetch_openrouter_radar()
    if radar:
        blocks.append(radar)
    blocks.append("\n<b>Как читать радар:</b> рейтинги OpenRouter показывают фактическое использование на платформе, а не качество моделей и не общее число пользователей.")
    return "\n".join(blocks)


def persist_chat_id_as_repository_variable(chat_id: str) -> None:
    """Persist the discovered chat ID as a GitHub Actions repository variable."""
    if not GITHUB_TOKEN or not GITHUB_REPOSITORY:
        print("GITHUB_TOKEN or GITHUB_REPOSITORY unavailable; chat ID was not persisted.")
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
            response = SESSION.patch(api, headers=headers, json={"name": "TELEGRAM_CHAT_ID", "value": chat_id}, timeout=20)
        response.raise_for_status()
        print("Telegram chat ID saved to GitHub Actions Variables.")
    except requests.RequestException as exc:
        status = getattr(getattr(exc, "response", None), "status_code", "unknown")
        print(f"Could not persist Telegram chat ID in GitHub Actions Variables (HTTP {status}).")


def resolve_chat_id() -> str:
    """Use configured secret/variable, or discover the private chat once via getUpdates."""
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
        raise RuntimeError("Telegram getUpdates failed; check the bot token and try again.")
    private_chats = []
    for update in payload.get("result", []):
        message = update.get("message") or update.get("edited_message") or {}
        chat = message.get("chat") or {}
        if chat.get("type") == "private" and chat.get("id") is not None:
            private_chats.append(chat["id"])
    if not private_chats:
        raise RuntimeError("No private chat found. Open the bot in Telegram, press Start, send it a message, then rerun the workflow.")
    discovered = str(private_chats[-1])
    persist_chat_id_as_repository_variable(discovered)
    print("Automatically discovered the private Telegram chat ID; it will not be printed to logs.")
    return discovered


def send_telegram(text: str) -> None:
    chat_id = resolve_chat_id()
    # Telegram message limit is 4096 chars. Split only between news blocks.
    chunks: list[str] = []
    current = ""
    for part in text.split("\n\n"):
        candidate = (current + "\n\n" + part).strip()
        if len(candidate) > 3900 and current:
            chunks.append(current)
            current = part
        else:
            current = candidate
    if current:
        chunks.append(current)
    for chunk in chunks:
        response = SESSION.post(
            f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage",
            json={"chat_id": chat_id, "text": chunk, "parse_mode": "HTML", "disable_web_page_preview": True},
            timeout=30,
        )
        response.raise_for_status()
        payload = response.json()
        if not payload.get("ok"):
            raise RuntimeError(f"Telegram API error: {payload}")


def main() -> None:
    news = fetch_news()
    print(f"Found {len(news)} candidate stories in the last {WINDOW_HOURS} hours")
    digest = build_digest(news)
    send_telegram(digest)
    print("Digest sent.")


if __name__ == "__main__":
    main()
