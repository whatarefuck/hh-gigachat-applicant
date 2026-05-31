"""Внешние уведомления и трекинг ошибок.

- **Sentry** — автоматический сбор ошибок (ERROR-логи + unhandled exceptions).
  Конфиг: `notify.sentry.dsn` в config.json.
- **Telegram** — отправка произвольных сообщений (используется ассистентом и
  командой `notify-test`). Конфиг: `notify.telegram.{bot_token, chat_id}`.

```json
"notify": {
  "sentry": {
    "dsn": "https://...@oXXXX.ingest.de.sentry.io/XXXX"
  },
  "telegram": {
    "bot_token": "1234:AAH...",
    "chat_id": 123456789
  }
}
```
"""

from __future__ import annotations

import logging
from typing import Any

import requests

logger = logging.getLogger(__package__)

_TELEGRAM_MAX_LEN = 4000  # лимит Telegram 4096, оставляем запас


def init_sentry(config: dict[str, Any]) -> bool:
    """Инициализирует Sentry, если задан `notify.sentry.dsn`.

    ERROR-логи и необработанные исключения уходят в Sentry автоматически.
    Возвращает True, если Sentry подключён.
    """
    notify = config.get("notify") or {}
    if not isinstance(notify, dict):
        return False
    sentry_cfg = notify.get("sentry") or {}
    if not isinstance(sentry_cfg, dict):
        return False
    dsn = sentry_cfg.get("dsn")
    if not dsn:
        return False

    try:
        import sentry_sdk
        from sentry_sdk.integrations.logging import LoggingIntegration
    except ImportError:
        logger.warning(
            "notify.sentry.dsn задан, но пакет sentry-sdk не установлен. "
            "Установи: pip install sentry-sdk"
        )
        return False

    logging_integration = LoggingIntegration(
        level=logging.INFO,  # INFO+ как breadcrumbs
        event_level=logging.ERROR,  # ERROR+ как события
    )
    sentry_sdk.init(
        dsn=str(dsn),
        integrations=[logging_integration],
        traces_sample_rate=float(sentry_cfg.get("traces_sample_rate", 0.0)),
        send_default_pii=bool(sentry_cfg.get("send_default_pii", False)),
        environment=sentry_cfg.get("environment", "production"),
    )
    logger.debug("Sentry подключён (ERROR-логи + исключения).")
    return True


def send_telegram_message(config: dict[str, Any], text: str) -> bool:
    """Шлёт текст в Telegram по `notify.telegram.{bot_token, chat_id}`.

    Возвращает True при успехе. Сетевые ошибки логируются, не пробрасываются.
    """
    notify = config.get("notify") or {}
    telegram_cfg = (
        notify.get("telegram") if isinstance(notify, dict) else None
    )
    if not isinstance(telegram_cfg, dict):
        logger.warning("notify.telegram не настроен — сообщение не отправлено.")
        return False

    token = telegram_cfg.get("bot_token")
    chat_id = telegram_cfg.get("chat_id")
    if not token or chat_id in (None, ""):
        logger.warning(
            "notify.telegram.{bot_token,chat_id} не заданы — сообщение не отправлено."
        )
        return False

    if len(text) > _TELEGRAM_MAX_LEN:
        text = text[: _TELEGRAM_MAX_LEN - 5] + "\n[…]"

    try:
        response = requests.post(
            f"https://api.telegram.org/bot{token}/sendMessage",
            json={
                "chat_id": str(chat_id),
                "text": text,
                "disable_web_page_preview": True,
            },
            timeout=10.0,
        )
        response.raise_for_status()
    except requests.exceptions.RequestException as ex:
        logger.error("Не удалось отправить сообщение в Telegram: %s", ex)
        return False
    return True
