import html
from typing import Any, Dict

from .http import get_json, post_json, urlencode


TELEGRAM_LIMIT = 3900


def split_message(text: str, limit: int = TELEGRAM_LIMIT):
    current = []
    size = 0
    for line in text.splitlines():
        extra = len(line) + 1
        if current and size + extra > limit:
            yield "\n".join(current)
            current = []
            size = 0
        current.append(line)
        size += extra
    if current:
        yield "\n".join(current)


def format_telegram_html(text: str) -> str:
    lines = []
    for line in text.splitlines():
        escaped = html.escape(line)
        if line.startswith("[") and line.endswith("]"):
            lines.append(f"<b>{escaped}</b>")
        elif line.startswith("판단:"):
            lines.append(f"<b>{escaped}</b>")
        else:
            lines.append(escaped)
    return "\n".join(lines)


def send_message(bot_token: str, chat_id: str, text: str) -> None:
    if not bot_token or not chat_id:
        raise ValueError("TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID are required")
    url = f"https://api.telegram.org/bot{bot_token}/sendMessage"
    for part in split_message(format_telegram_html(text)):
        payload = {
            "chat_id": chat_id,
            "text": part,
            "parse_mode": "HTML",
            "disable_web_page_preview": True,
        }
        result = post_json(url, payload, timeout=30)
        if not result.get("ok"):
            raise RuntimeError(f"Telegram sendMessage failed: {result}")


def check_connection(bot_token: str, chat_id: str) -> None:
    if not bot_token or not chat_id:
        raise ValueError("TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID are required")

    me = get_json(f"https://api.telegram.org/bot{bot_token}/getMe", timeout=30)
    if not me.get("ok"):
        raise RuntimeError(f"Telegram bot token check failed: {me}")

    chat = get_json(
        f"https://api.telegram.org/bot{bot_token}/getChat?{urlencode({'chat_id': chat_id})}",
        timeout=30,
    )
    if not chat.get("ok"):
        raise RuntimeError(f"Telegram chat check failed: {chat}")


def get_updates(bot_token: str) -> Dict[str, Any]:
    if not bot_token:
        raise ValueError("TELEGRAM_BOT_TOKEN is required")
    return get_json(f"https://api.telegram.org/bot{bot_token}/getUpdates", timeout=30)
