from typing import Any, Dict

from .collectors import collect_all
from .config import get_settings
from .shorts_storage import save_shorts_package_to_dynamodb
from .telegram import send_message
from .youtube import build_youtube_short_package


def handler(event: Dict[str, Any], context: Any) -> Dict[str, Any]:
    settings = get_settings()
    data = collect_all()
    package = build_youtube_short_package(
        data,
        persona_id=settings.youtube_persona,
        news_limit=max(1, settings.shorts_news_limit),
    )
    save_shorts_package_to_dynamodb(package, table_name=settings.shorts_table_name)
    try:
        if settings.telegram_bot_token and settings.telegram_chat_id:
            send_message(
                settings.telegram_bot_token,
                settings.telegram_chat_id,
                f"[쇼츠 생성 완료]\n{package['title']}\n{package['render']['target_duration_sec']}초 / {len(package['selected_news'])}개 뉴스",
            )
    except Exception as exc:
        return {
            "ok": False,
            "error": f"Telegram 연결이 끊겼습니다: {exc}",
            "generated_at": data.get("generated_at"),
            "message_chars": len(package["script"]["tts_text"]),
            "event_id": event.get("id") if isinstance(event, dict) else None,
        }
    return {
        "ok": True,
        "generated_at": data.get("generated_at"),
        "message_chars": len(package["script"]["tts_text"]),
        "event_id": event.get("id") if isinstance(event, dict) else None,
        "title": package["title"],
        "selected_news": len(package["selected_news"]),
    }
