from .config import get_settings
from .telegram import get_updates


def main() -> int:
    settings = get_settings()
    updates = get_updates(settings.telegram_bot_token)
    results = updates.get("result", [])
    if not results:
        print("No Telegram updates found. Send a message to your bot first, then run this again.")
        return 1

    seen = set()
    for item in results:
        message = item.get("message") or item.get("channel_post") or {}
        chat = message.get("chat") or {}
        chat_id = chat.get("id")
        if not chat_id or chat_id in seen:
            continue
        seen.add(chat_id)
        title = chat.get("title") or chat.get("username") or chat.get("first_name") or "unknown"
        print(f"TELEGRAM_CHAT_ID={chat_id}  # {title}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

