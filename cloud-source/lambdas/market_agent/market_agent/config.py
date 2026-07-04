from dataclasses import dataclass
import os
from pathlib import Path
from zoneinfo import ZoneInfo


ROOT = Path(__file__).resolve().parents[1]
REPORTS_DIR = ROOT / "reports"
DECISIONS_DIR = ROOT / "decision_snapshots"
PERFORMANCE_DIR = ROOT / "performance"
SHORTS_DIR = ROOT / "shorts"
YOUTUBE_DIR = ROOT / ".youtube"
DEFAULT_YOUTUBE_CLIENT_SECRETS_FILE = YOUTUBE_DIR / "client_secret.json"
DEFAULT_YOUTUBE_TOKEN_FILE = YOUTUBE_DIR / "token.json"
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
    youtube_persona: str
    shorts_table_name: str
    shorts_news_limit: int
    youtube_client_secrets_file: str
    youtube_token_file: str


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
        youtube_persona=os.getenv("YOUTUBE_PERSONA", "economy_host"),
        shorts_table_name=os.getenv("SHORTS_TABLE_NAME", ""),
        shorts_news_limit=int(os.getenv("SHORTS_NEWS_LIMIT", "3")),
        youtube_client_secrets_file=os.getenv(
            "YOUTUBE_CLIENT_SECRETS_FILE",
            str(DEFAULT_YOUTUBE_CLIENT_SECRETS_FILE),
        ),
        youtube_token_file=os.getenv("YOUTUBE_TOKEN_FILE", str(DEFAULT_YOUTUBE_TOKEN_FILE)),
    )
