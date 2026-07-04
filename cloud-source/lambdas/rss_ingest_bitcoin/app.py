# app.py (Lite: RSS 제목/요약 저장용)
import os, json, gzip, hashlib, datetime
from io import BytesIO
from urllib.parse import urlparse

import boto3
import feedparser
from botocore.config import Config
from bs4 import BeautifulSoup  # 순수 python 파서 사용(내장 html.parser)

# ===== 환경변수 =====
BUCKET = os.environ["BUCKET"]                           # 예: news-pipeline-kr
PREFIX = os.environ.get("PREFIX", "kr/bitcoin")         # 예: kr/bitcoin
FEEDS  = [u.strip() for u in os.environ.get(
    "FEEDS",
    "https://kr.cointelegraph.com/rss/tag/bitcoin"
).split(",") if u.strip()]
MAX_ITEMS = int(os.environ.get("MAX_ITEMS", "200"))

cfg = Config(retries={'max_attempts': 3})
s3 = boto3.client('s3', config=cfg)

def feed_slug_from_url(url: str) -> str:
    try:
        path = urlparse(url).path.strip("/")
        slug = (path.split("/")[-1] or "feed").lower()
        slug = "".join(ch for ch in slug if ch.isalnum() or ch in ("-", "_"))
        return slug or "feed"
    except Exception:
        return "feed"

def html_to_text(html: str) -> str:
    if not html:
        return ""
    # lxml 미사용: 내장 파서 'html.parser'로 동작 → 레이어 없이도 OK
    soup = BeautifulSoup(html, "html.parser")
    return soup.get_text(" ", strip=True)

def normalize(entry, source_name, doc_type="crypto"):
    title = (entry.get("title") or "").strip()
    # RSS description이 보통 HTML → 텍스트 변환
    summary_html = entry.get("summary") or entry.get("description") or ""
    if not summary_html and entry.get("content"):
        try:
            summary_html = entry["content"][0].get("value") or ""
        except Exception:
            pass
    summary_text = html_to_text(summary_html)

    pub = entry.get("published") or entry.get("updated") or ""
    link = entry.get("link") or ""

    return {
        "type": doc_type,
        "source": source_name,
        "title": title,
        "summary_text": summary_text,     # 사람이 보기 좋은 요약
        "summary_html": summary_html,     # 원본 HTML(원하면 나중에 사용)
        "link": link,
        "published_at": pub,
    }

def fetch_and_dedup_per_feed(feeds):
    buckets, seen = {}, {}
    for url in feeds:
        feed_slug = feed_slug_from_url(url)
        buckets.setdefault(feed_slug, [])
        seen.setdefault(feed_slug, set())
        try:
            f = feedparser.parse(url)
            source_name = (getattr(f, "feed", {}) or {}).get("title") or url

            for e in f.entries[:MAX_ITEMS]:
                d = normalize(e, source_name, doc_type="crypto")
                d["source_url"] = url
                d["feed_slug"]  = feed_slug

                # 비트코인 관련성 보강(안전장치)
                text_for_check = (d["title"] + " " + d.get("summary_text","")).lower()
                if not any(k in text_for_check for k in ["bitcoin", "비트코인", "btc"]):
                    # 코인텔레그래프 비트코인 태그 피드면 거의 필요없지만, 혹시 몰라 가드만 둠
                    pass

                # 실행 내 중복(제목+요약) 제거
                basis = (d.get("title","") + d.get("summary_text","")).strip()
                if not basis:
                    continue
                h = hashlib.sha256(basis.encode("utf-8")).hexdigest()[:16]
                if h in seen[feed_slug]:
                    continue
                seen[feed_slug].add(h)

                buckets[feed_slug].append(d)
        except Exception as ex:
            print("Feed error:", url, ex)
    return buckets

def write_jsonl_gzip_per_feed(prefix, records_by_feed):
    now = datetime.datetime.utcnow()
    day = now.strftime("%Y-%m-%d")
    ts  = now.strftime("%H%M%S")
    saved = []
    for feed_slug, records in records_by_feed.items():
        if not records:
            continue
        key = f"{prefix}/{day}/{feed_slug}/raw/batch_{ts}.jsonl.gz"

        buf = BytesIO()
        with gzip.GzipFile(fileobj=buf, mode="w") as gz:
            for r in records:
                gz.write((json.dumps(r, ensure_ascii=False) + "\n").encode("utf-8"))

        s3.put_object(Bucket=BUCKET, Key=key, Body=buf.getvalue())
        saved.append({"feed": feed_slug, "count": len(records), "s3_key": key})
    return saved

def run_pipeline(feeds, prefix):
    records_by_feed = fetch_and_dedup_per_feed(feeds)
    saved = write_jsonl_gzip_per_feed(prefix, records_by_feed)
    total = sum(x["count"] for x in saved)
    return {"total_count": total, "saved": saved, "prefix": prefix}

def handler(event, context):
    result = run_pipeline(FEEDS, PREFIX)
    result["lambda"] = "bitcoin-rss-lite"
    return result

if __name__ == "__main__":
    import json as _json
    print(_json.dumps(handler({}, {}), ensure_ascii=False, indent=2))
