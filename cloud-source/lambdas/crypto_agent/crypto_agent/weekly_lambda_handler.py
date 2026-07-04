from typing import Any, Dict

from .config import get_settings
from .evaluation import build_weekly_performance_report, save_performance_report_to_s3


def handler(event: Dict[str, Any], context: Any) -> Dict[str, Any]:
    settings = get_settings()
    payload = build_weekly_performance_report(table_name=settings.history_table_name)
    save_performance_report_to_s3(payload, bucket_name=settings.performance_bucket_name)
    return {
        "ok": True,
        "generated_at": payload.get("generated_at"),
        "message_chars": len(payload["report_text"]),
        "event_id": event.get("id") if isinstance(event, dict) else None,
    }
