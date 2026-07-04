import os, json, gzip, hashlib, datetime
import boto3, feedparser
from io import BytesIO
from botocore.config import Config
from urllib.parse import urlparse

# --- Environment variables ---
BUCKET = os.environ["BUCKET"]                       # 예: news-pipeline-kr
PREFIX = os.environ.get("PREFIX", "herald")
FEEDS  = [u.strip() for u in os.environ["FEEDS"].split(",") if u.strip()]
# FEEDS 예: https://biz.heraldcorp.com/rss/google/economy,https://biz.heraldcorp.com/rss/google/finance

cfg = Config(retries={'max_attempts': 3})
s3 = boto3.client('s3', config=cfg)

def feed_slug_from_url(url: str) -> str:
    """URL 경로의 마지막 세그먼트를 슬러그로 사용 (예: .../economy -> 'economy')."""
    try:
        path = urlparse(url).path.strip("/")
        # path 예: "rss/google/economy"
        slug = (path.split("/")[-1] or "unknown").lower()
        # 혹시 쿼리/확장자 등 변수가 있으면 간단히 정리
        slug = "".join(ch for ch in slug if ch.isalnum() or ch in ("-", "_"))
        return slug or "unknown"
    except Exception:
        return "unknown"

def normalize(entry, source_name):
    title = (entry.get("title") or "").strip()
    body  = (entry.get("summary") or entry.get("description") or "").strip()
    pub   = entry.get("published") or entry.get("updated") or ""
    link  = entry.get("link") or ""
    return {
        "type": "company",
        "source": source_name,
        "title": title,
        "body": body,
        "link": link,
        "published_at": pub,
    }

def fetch_and_dedup_per_feed(feeds):
    """
    피드별로 문서를 모으고, 피드별로 중복 제거합니다.
    반환: {feed_slug: [records...]} 형태
    """
    buckets = {}              # feed_slug -> list[doc]
    seen_by_feed = {}         # feed_slug -> set(hashes)

    for url in feeds:
        feed_slug = feed_slug_from_url(url)   # economy, finance 등
        buckets.setdefault(feed_slug, [])
        seen_by_feed.setdefault(feed_slug, set())

        try:
            f = feedparser.parse(url)
            source_name = (getattr(f, "feed", {}) or {}).get("title") or url

            for e in f.entries:
                d = normalize(e, source_name)
                d["source_url"] = url
                d["feed_slug"]  = feed_slug

                # 빈 제목/본문은 스킵
                if not d["title"] and not d["body"]:
                    continue

                h = hashlib.sha256((d["title"] + d["body"]).encode()).hexdigest()[:16]
                if h in seen_by_feed[feed_slug]:
                    continue  # 같은 피드 내 중복 제거
                seen_by_feed[feed_slug].add(h)

                buckets[feed_slug].append(d)
        except Exception as ex:
            print("Feed error:", url, ex)

    return buckets

def write_jsonl_gzip_per_feed(prefix, records_by_feed):
    """
    feed_slug 별로 파일을 분리해 저장.
    키 예: {prefix}/{YYYY-MM-DD}/{feed_slug}/raw/batch_{HHMMSS}.jsonl.gz
    """
    day = datetime.datetime.now().strftime("%Y-%m-%d")
    ts  = datetime.datetime.now().strftime("%H%M%S")
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

def handler(event, context):
    records_by_feed = fetch_and_dedup_per_feed(FEEDS)
    saved = write_jsonl_gzip_per_feed(PREFIX, records_by_feed)
    total = sum(x["count"] for x in saved)
    return {"total_count": total, "saved": saved}
