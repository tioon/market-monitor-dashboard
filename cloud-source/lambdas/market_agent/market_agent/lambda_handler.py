from typing import Any, Dict

from .collectors import collect_all
from .config import get_settings
from .report import build_report
from .storage import save_report_to_dynamodb
from .telegram import send_message


def handler(event: Dict[str, Any], context: Any) -> Dict[str, Any]:
    settings = get_settings()
    data = collect_all()
    report, decision_snapshot = build_report(
        data,
        api_key=settings.openai_api_key,
        model=settings.openai_model,
        concise=True,
        return_snapshot=True,
    )
    save_report_to_dynamodb(
        data,
        report,
        table_name=settings.history_table_name,
        decision_table_name=settings.decision_table_name,
        decision_snapshot=decision_snapshot,
    )
    try:
        send_message(settings.telegram_bot_token, settings.telegram_chat_id, report)
    except Exception as exc:
        return {
            "ok": False,
            "error": f"Telegram 연결이 끊겼습니다: {exc}",
            "generated_at": data.get("generated_at"),
            "message_chars": len(report),
            "event_id": event.get("id") if isinstance(event, dict) else None,
        }
    return {
        "ok": True,
        "generated_at": data.get("generated_at"),
        "message_chars": len(report),
        "event_id": event.get("id") if isinstance(event, dict) else None,
    }
