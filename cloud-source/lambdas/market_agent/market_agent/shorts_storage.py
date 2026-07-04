import datetime as dt
from decimal import Decimal
from typing import Any, Dict


def _convert(value: Any) -> Any:
    if isinstance(value, float):
        return Decimal(str(value))
    if isinstance(value, dict):
        return {key: _convert(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_convert(item) for item in value]
    return value


def _persist_table_item(table_name: str, item: Dict[str, Any]) -> None:
    try:
        import boto3  # type: ignore
    except Exception:
        return
    session = boto3.session.Session()
    resource = session.resource("dynamodb")
    table = resource.Table(table_name)
    table.put_item(Item=_convert(item))


def save_shorts_package_to_dynamodb(package: Dict[str, Any], table_name: str) -> None:
    if not table_name:
        return
    generated_at = package.get("generated_at") or dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")
    _persist_table_item(
        table_name,
        {
            "project_id": "market-agent",
            "record_key": f"SHORTS#{generated_at}",
            "record_type": "youtube_shorts_package",
            "generated_at": package.get("generated_at"),
            "package": package,
        },
    )
