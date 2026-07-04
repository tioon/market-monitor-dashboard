# ─────────────────────────────────────────────
# RSS 뉴스를 수집해서 Gmail SMTP로 이메일 전송
# - 섹터별 최대 N개
# - 피드 전체에서 제목 유사도 기반 중복 제거
# ─────────────────────────────────────────────

import os, json, urllib.request, urllib.error, socket, smtplib, re
import feedparser
from difflib import SequenceMatcher
from email.mime.text import MIMEText
from email.utils import formatdate
from datetime import datetime, timezone, timedelta

# ==== 환경변수 ====
GMAIL_USER = os.getenv("GMAIL_USER")                 # 예: tioon75@gmail.com
GMAIL_APP_PASSWORD = os.getenv("GMAIL_APP_PASSWORD") # 앱 비밀번호(16자리)
TO_EMAIL = os.getenv("TO_EMAIL")

FEED_URLS = [u.strip() for u in os.getenv("FEED_URLS", "").split(",") if u.strip()]
MAX_PER_FEED = int(os.getenv("MAX_PER_FEED", "25"))
REQUEST_TIMEOUT = int(os.getenv("REQUEST_TIMEOUT", "8"))

# URL → 섹션 제목 강제 매핑(JSON)
# 예시:
# {"https://.../economy.xml":"연합뉴스 경제 최신기사", "...":"매일경제 : 증권", ...}
FEED_TITLE_OVERRIDES = json.loads(os.getenv("FEED_TITLE_OVERRIDES", "{}"))

# 전역 소켓 타임아웃(안전망)
socket.setdefaulttimeout(REQUEST_TIMEOUT)

# 일부 사이트가 UA 없으면 차단되므로 헤더 설정
UA = "Mozilla/5.0 (compatible; RSSMailer/1.0; +https://example.com)"

# KST 타임존
KST = timezone(timedelta(hours=9))


# ─────────────────────────────────────────────
# 유틸 함수들
# ─────────────────────────────────────────────

def _clean(text, default=""):
    return (text or default).replace("\n", " ").strip()


def normalize_title(t: str) -> str:
    """
    제목에서 괄호/대괄호 태그, 특수문자, 중복 공백 제거해서
    비교용 문자열로 변환 (한글/영문/숫자 위주로 정규화)
    """
    if not t:
        return ""
    t = t.lower()

    # [단독], [속보], (영상) 같은 태그 제거
    t = re.sub(r'\[[^\]]*\]', ' ', t)
    t = re.sub(r'\([^)]*\)', ' ', t)

    # 특수문자 정리
    t = re.sub(r'["\'“”‘’·…]+', ' ', t)
    t = re.sub(r'[^가-힣a-z0-9 ]+', ' ', t)

    # 공백 정리
    t = re.sub(r'\s+', ' ', t).strip()
    return t


def is_similar_title(new_title: str, seen_norm_titles: list, threshold: float = 0.85) -> bool:
    """
    이미 선택된 기사들의 '정규화된 제목' 리스트(seen_norm_titles)와 비교해서
    가장 비슷한 제목의 유사도가 threshold 이상이면 True (즉, 중복/유사 기사로 간주)
    """
    n = normalize_title(new_title)
    if not n:
        return False

    for s in seen_norm_titles:
        if SequenceMatcher(None, n, s).ratio() >= threshold:
            return True
    return False


def build_section(feed_title, items):
    lis = []
    for it in items:
        title = _clean(getattr(it, "title", None), "(제목 없음)")
        link  = getattr(it, "link", "#") or "#"
        summary = _clean(
            getattr(it, "summary", None)
            or getattr(it, "description", None)
            or "",
            "",
        )
        if len(summary) > 240:
            summary = summary[:240] + "…"
        lis.append(
            f"<li style='margin:8px 0'>"
            f"<a href='{link}'>{title}</a><br>"
            f"<small style='color:#555'>{summary}</small></li>"
        )
    return (
        f"<h3 style='margin:16px 0'>{feed_title}</h3>"
        f"<ul style='padding-left:18px'>{''.join(lis)}</ul>"
    )


def fetch_feed(feed_url):
    # 명시적 타임아웃 + UA로 직접 요청 후 feedparser에 전달
    req = urllib.request.Request(feed_url, headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=REQUEST_TIMEOUT) as resp:
        data = resp.read()
    return feedparser.parse(data)


def send_via_smtp(subject, html):
    assert GMAIL_USER and GMAIL_APP_PASSWORD and TO_EMAIL, "Missing SMTP env vars"
    msg = MIMEText(html, "html", "utf-8")
    msg["Subject"] = subject
    msg["From"] = GMAIL_USER
    msg["To"] = TO_EMAIL
    msg["Date"] = formatdate(localtime=True)

    with smtplib.SMTP("smtp.gmail.com", 587, timeout=15) as s:
        s.starttls()
        s.login(GMAIL_USER, GMAIL_APP_PASSWORD)
        s.sendmail(GMAIL_USER, [TO_EMAIL], msg.as_string())
    return 250  # SMTP OK


# ─────────────────────────────────────────────
# Lambda 핸들러
# ─────────────────────────────────────────────

def handler(event, context):
    sections = []
    total = 0
    failed_feeds = []

    # 전 피드에 걸쳐서 관리할 전역 중복 기준
    seen_links = set()        # 정규화된 링크
    seen_norm_titles = []     # 정규화된 제목 문자열

    for feed_url in FEED_URLS:
        try:
            parsed = fetch_feed(feed_url)

            # 1) URL 매핑 우선
            feed_title = FEED_TITLE_OVERRIDES.get(feed_url)
            # 2) 매핑 없으면 원래 피드 제목 사용
            if not feed_title:
                feed_title = getattr(parsed.feed, "title", feed_url)

            unique_entries = []

            for it in parsed.entries:
                raw_link = getattr(it, "link", "") or ""
                # 쿼리스트링/앵커 제거해서 링크 정규화
                normalized_link = raw_link.split("?", 1)[0].split("#", 1)[0]

                title = _clean(getattr(it, "title", None), "(제목 없음)")

                # 1) 링크 완전 중복 먼저 제거 (있으면 가장 강력한 기준)
                if normalized_link and normalized_link in seen_links:
                    continue

                # 2) 제목 유사도 기반 중복 제거
                #    이미 뽑힌 기사들과 제목이 거의 같으면 스킵
                if is_similar_title(title, seen_norm_titles, threshold=0.85):
                    continue

                # 여기까지 왔다면 "새로운" 기사로 채택
                if normalized_link:
                    seen_links.add(normalized_link)

                seen_norm_titles.append(normalize_title(title))
                unique_entries.append(it)

                # 이 섹터에서 MAX_PER_FEED(예: 20)개 채우면 종료
                if len(unique_entries) >= MAX_PER_FEED:
                    break

            sections.append(build_section(feed_title, unique_entries))
            total += len(unique_entries)

        except Exception as e:
            failed_feeds.append({"feed": feed_url, "error": str(e)})

    # 생성시각을 KST로 표기
    kst_now = datetime.now(KST).strftime("%Y-%m-%d %H:%M:%S %Z")

    html = (
        "<html><body style='font-family:system-ui,Segoe UI,Apple SD Gothic Neo,Malgun Gothic,sans-serif'>"
        "<h2 style='margin:0 0 4px'>아침 뉴스 요약</h2>"
        f"<div style='color:#666;font-size:12px;margin-bottom:12px'>"
        f"생성시각: {kst_now} · 총 {total}건</div>"
        f"{''.join(sections)}"
    )

    if failed_feeds:
        html += (
            "<hr style='margin:16px 0'><div style='color:#a00'>"
            "<b>일부 피드 로딩 실패</b><ul>"
            + "".join(f"<li>{f['feed']}: {f['error']}</li>" for f in failed_feeds)
            + "</ul></div>"
        )

    html += (
        "<hr style='margin:16px 0'>"
        "<div style='color:#888;font-size:12px'>자동 발송 알림</div>"
        "</body></html>"
    )

    status = send_via_smtp("[RSS] 평일 07:00 아침 뉴스 요약", html)
    return {"status": status, "total_items": total, "failed": failed_feeds}


# 별칭 핸들러 (런타임 설정이 lambda_function.lambda_handler여도 동작)
def lambda_handler(event, context):
    return handler(event, context)
