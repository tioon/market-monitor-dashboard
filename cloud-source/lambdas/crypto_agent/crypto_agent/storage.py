import datetime as dt
import json
import re
from decimal import Decimal
from pathlib import Path
from typing import Any, Dict, Optional, Tuple

from .config import DECISIONS_DIR, REPORTS_DIR, REPORT_TIMEZONE


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


def _persist_history_item(data: Dict[str, Any], report: str, table_name: str) -> None:
    generated_at = data.get("generated_at") or dt.datetime.now(REPORT_TIMEZONE).isoformat(timespec="seconds")
    _persist_table_item(
        table_name,
        {
            "project_id": "crypto-agent",
            "record_key": f"REPORT#{generated_at}",
            "record_type": "report",
            "generated_at": generated_at,
            "report_text": report,
            "data": data,
        },
    )


def _persist_decision_item(snapshot: Dict[str, Any], table_name: str) -> None:
    generated_at = snapshot.get("generated_at") or dt.datetime.now(REPORT_TIMEZONE).isoformat(timespec="seconds")
    _persist_table_item(
        table_name,
        {
            "project_id": "crypto-agent",
            "record_key": f"DECISION#{generated_at}",
            "record_type": "decision",
            "generated_at": generated_at,
            "decision_snapshot": snapshot,
        },
    )


def save_report_to_dynamodb(
    data: Dict[str, Any],
    report: str,
    table_name: str = "",
    decision_table_name: str = "",
    decision_snapshot: Optional[Dict[str, Any]] = None,
) -> None:
    if table_name:
        _persist_history_item(data, report, table_name)
    if decision_table_name and decision_snapshot is not None:
        _persist_decision_item(decision_snapshot, decision_table_name)


def _prune_daily_reports(reports_dir: Path) -> None:
    latest_by_date = {}
    for pattern in ("20??-??-??_*.md", "20??-??-??_*.json"):
        for path in reports_dir.glob(pattern):
            if path.name.startswith("latest.") or path.suffix == ".swp":
                continue
            match = re.match(r"(?P<date>\d{4}-\d{2}-\d{2})_(?P<stamp>\d{6})\.(?P<ext>md|json)$", path.name)
            if not match:
                continue
            key = match.group("date")
            stamp = match.group("stamp")
            current = latest_by_date.get(key)
            if current is None or stamp > current[0]:
                latest_by_date[key] = (stamp, path)

    keep = {item[1].stem.rsplit(".", 1)[0] for item in latest_by_date.values()}
    for pattern in ("20??-??-??_*.md", "20??-??-??_*.json"):
        for path in reports_dir.glob(pattern):
            if path.name.startswith("latest.") or path.suffix == ".swp":
                continue
            stem = path.stem
            if stem not in keep:
                try:
                    path.unlink()
                except FileNotFoundError:
                    pass


def save_report(
    data: Dict[str, Any],
    report: str,
    reports_dir: Path = REPORTS_DIR,
    history_table_name: str = "",
    decision_table_name: str = "",
    decision_snapshot: Optional[Dict[str, Any]] = None,
) -> Tuple[Path, Path]:
    reports_dir.mkdir(parents=True, exist_ok=True)
    DECISIONS_DIR.mkdir(parents=True, exist_ok=True)
    stamp = dt.datetime.now(REPORT_TIMEZONE).strftime("%Y-%m-%d_%H%M%S")
    json_path = reports_dir / f"{stamp}.json"
    md_path = reports_dir / f"{stamp}.md"
    json_path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    md_path.write_text(report + "\n", encoding="utf-8")
    latest_md = reports_dir / "latest.md"
    latest_json = reports_dir / "latest.json"
    latest_md.write_text(report + "\n", encoding="utf-8")
    latest_json.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    if history_table_name:
        _persist_history_item(data, report, history_table_name)
    if decision_snapshot is not None:
        decision_json_path = DECISIONS_DIR / f"{stamp}.json"
        decision_json_path.write_text(json.dumps(decision_snapshot, ensure_ascii=False, indent=2), encoding="utf-8")
        (DECISIONS_DIR / "latest.json").write_text(json.dumps(decision_snapshot, ensure_ascii=False, indent=2), encoding="utf-8")
        if decision_table_name:
            _persist_decision_item(decision_snapshot, decision_table_name)
    _prune_daily_reports(reports_dir)
    _prune_daily_reports(DECISIONS_DIR)
    return md_path, json_path
