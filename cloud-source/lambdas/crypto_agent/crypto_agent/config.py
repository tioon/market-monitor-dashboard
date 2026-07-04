from dataclasses import dataclass
import os
from pathlib import Path
from zoneinfo import ZoneInfo


ROOT = Path(__file__).resolve().parents[1]
REPORTS_DIR = ROOT / "reports"
DECISIONS_DIR = ROOT / "decision_snapshots"
PERFORMANCE_DIR = ROOT / "performance"
REPORT_TIMEZONE = ZoneInfo("Asia/Seoul")


def load_dotenv(path: Path = ROOT / ".env") -> None:
    if not path.exists():
        return
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if key and key not in os.environ:
            os.environ[key] = value


@dataclass(frozen=True)
class Settings:
    telegram_bot_token: str
    telegram_chat_id: str
    openai_api_key: str
    openai_model: str
    history_table_name: str
    decision_table_name: str
    performance_bucket_name: str


def get_settings() -> Settings:
    load_dotenv()
    return Settings(
        telegram_bot_token=os.getenv("TELEGRAM_BOT_TOKEN", ""),
        telegram_chat_id=os.getenv("TELEGRAM_CHAT_ID", ""),
        openai_api_key=os.getenv("OPENAI_API_KEY", ""),
        openai_model=os.getenv("OPENAI_MODEL", "gpt-4.1-mini"),
        history_table_name=os.getenv("DYNAMODB_TABLE_NAME", ""),
        decision_table_name=os.getenv("DYNAMODB_DECISION_TABLE_NAME", ""),
        performance_bucket_name=os.getenv("PERFORMANCE_BUCKET_NAME", ""),
    )
