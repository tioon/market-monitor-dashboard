from typing import Any, Dict

from .config import get_settings
from .collectors import collect_crypto_all
from .report import build_crypto_report
from .storage import save_report_to_dynamodb
from .telegram import send_message


def handler(event: Dict[str, Any], context: Any) -> Dict[str, Any]:
    settings = get_settings()
    data = collect_crypto_all()
    report, decision_snapshot = build_crypto_report(
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
    send_message(settings.telegram_bot_token, settings.telegram_chat_id, report)
    return {
        "ok": True,
        "generated_at": data.get("generated_at"),
        "message_chars": len(report),
        "event_id": event.get("id") if isinstance(event, dict) else None,
    }
