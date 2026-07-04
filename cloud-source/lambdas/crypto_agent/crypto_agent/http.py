import json
import time
import urllib.error
import urllib.parse
import urllib.request
from typing import Any, Dict, Optional


USER_AGENT = "market-agent/0.1 (+local personal market briefing)"


def get_text(url: str, timeout: int = 20, retries: int = 2) -> str:
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    last_exc: Optional[BaseException] = None
    for attempt in range(retries + 1):
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                return resp.read().decode("utf-8", errors="replace")
        except urllib.error.HTTPError:
            raise
        except (OSError, TimeoutError) as exc:
            last_exc = exc
            if attempt >= retries:
                break
            time.sleep(0.6 * (attempt + 1))
    raise RuntimeError(f"Failed to fetch {url}: {last_exc}") from last_exc


def get_json(url: str, timeout: int = 20, retries: int = 2) -> Dict[str, Any]:
    return json.loads(get_text(url, timeout=timeout, retries=retries))


def post_json(url: str, payload: Dict[str, Any], timeout: int = 30, bearer: Optional[str] = None) -> Dict[str, Any]:
    data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    headers = {"Content-Type": "application/json", "User-Agent": USER_AGENT}
    if bearer:
        headers["Authorization"] = f"Bearer {bearer}"
    req = urllib.request.Request(url, data=data, headers=headers, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            body = resp.read().decode("utf-8", errors="replace")
            return json.loads(body) if body else {}
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"HTTP {exc.code} from {url}: {detail}") from exc


def urlencode(params: Dict[str, Any]) -> str:
    return urllib.parse.urlencode(params)
