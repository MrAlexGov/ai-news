# AI News Digest

Ежедневный международный дайджест главных новостей искусственного интеллекта с отправкой в Telegram через GitHub Actions.

## Setup

1. Создайте Telegram-бота через @BotFather и получите `TELEGRAM_BOT_TOKEN`.
2. Напишите боту `/start`, затем получите свой chat ID (например, через `https://api.telegram.org/bot<TOKEN>/getUpdates`).
3. В GitHub откройте **Settings → Secrets and variables → Actions** и добавьте secrets `TELEGRAM_BOT_TOKEN` и `TELEGRAM_CHAT_ID`.
4. Workflow запускается ежедневно в 07:00 UTC (10:00 по Москве) и вручную через Actions.

Проект собирает новости из RSS-источников и публичные обсуждения Hacker News. Он не использует платный LLM API: краткое описание строится по доступным описаниям публикаций, поэтому качество зависит от полноты RSS-метаданных.
