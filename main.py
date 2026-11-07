from __future__ import annotations

import html
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
CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "").strip()
WINDOW_HOURS = int(os.getenv("WINDOW_HOURS", "24"))
MAX_ITEMS = int(os.getenv("MAX_ITEMS", "10"))
UTC_NOW = datetime.now(timezone.utc)
CUTOFF = UTC_NOW - timedelta(hours=WINDOW_HOURS)
SESSION = requests.Session()
SESSION.headers.update({"User-Agent": "AI-News-Digest/1.0 (+https://github.com/MrAlexGov/ai-news)"})

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
]

AI_TERMS = re.compile(r"\b(ai|artificial intelligence|machine learning|llm|language model|agent|生成|ии|нейросет|deep learning|генеративн|робот|openai|anthropic|gemini|claude|gpt|llama|mistral|nvidia|deepmind)\b", re.I)


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
    return sorted(found.values(), key=lambda x: x["date"], reverse=True)


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
    header = f"🧠 <b>AI NEWS — {local_day}</b>\nЗа последние {WINDOW_HOURS} часов · международная подборка\n"
    if not items:
        return header + "\nЗа выбранные 24 часа не удалось подтвердить новости из подключённых источников. Попробуем в следующем выпуске."
    blocks = [header]
    for i, item in enumerate(items[:MAX_ITEMS], 1):
        summary = item["summary"] or "В RSS-ленте опубликована новая новость; подробности — по ссылке на первоисточник."
        summary = summary[:500]
        reaction = hn_reaction(item["title"])
        published = item["date"].strftime("%d.%m %H:%M UTC")
        block = (
            f"\n<b>{i}. {html.escape(item['title'])}</b>\n"
            f"<i>{html.escape(item['source'])} · {published}</i>\n"
            f"{html.escape(summary)}\n"
            f"🔗 <a href=\"{html.escape(item['url'], quote=True)}\">Первоисточник</a>"
        )
        if reaction:
            block += "\n💬 " + html.escape(reaction).replace("&lt;", "<").replace("&gt;", ">")
        else:
            block += "\n💬 Реакция сообщества: заметного обсуждения в Hacker News не найдено."
        blocks.append(block)
    blocks.append("\n<b>Важно:</b> подборка формируется по свежести публикаций из открытых RSS-источников; описания основаны на их аннотациях. Это не полноценный редакционный рейтинг.")
    return "\n".join(blocks)


def resolve_chat_id() -> str:
    """Use the configured chat ID, or discover the most recent private chat via getUpdates."""
    if CHAT_ID:
        return CHAT_ID
    response = SESSION.get(
        f"https://api.telegram.org/bot{BOT_TOKEN}/getUpdates",
        params={"limit": 100, "allowed_updates": ["message"]},
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
