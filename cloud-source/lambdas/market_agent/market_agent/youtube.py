import datetime as dt
import json
import re
from dataclasses import dataclass, asdict
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

from .config import REPORT_TIMEZONE, SHORTS_DIR
from .report import _market_core_snapshot, _plain_verdict, _risk_dashboard, _regime_label


SOURCE_WEIGHTS = {
    "reuters": 5,
    "ap": 4,
    "associated press": 4,
    "marketwatch": 3,
    "google news": 2,
    "cnbc": 3,
    "bloomberg": 4,
    "ft": 4,
    "wsj": 4,
    "investing": 2,
    "yahoo": 1,
}

MARKET_KEYWORDS = {
    "fed",
    "rate",
    "rates",
    "inflation",
    "cpi",
    "ppi",
    "jobs",
    "payroll",
    "yield",
    "treasury",
    "dollar",
    "oil",
    "gold",
    "earnings",
    "guidance",
    "tariff",
    "trade",
    "semiconductor",
    "chip",
    "ai",
    "bitcoin",
    "crypto",
    "stock",
    "stocks",
    "market",
    "markets",
    "vix",
    "recession",
    "debt",
    "credit",
}


@dataclass(frozen=True)
class PersonaProfile:
    persona_id: str
    name: str
    role: str
    tone: str
    voice_style: str
    intro_line: str
    closing_line: str
    catchphrase: str


PERSONA_LIBRARY = {
    "economy_host": PersonaProfile(
        persona_id="economy_host",
        name="경제형 호스트",
        role="친근한 경제 유튜버",
        tone="빠르고 또렷하게 핵심만 짚는 스타일",
        voice_style="약간 빠른 템포, 단정한 발음, 과장 없이 자신감 있게",
        intro_line="좋아요. 오늘 시장, 핵심만 빠르게 정리해볼게요.",
        closing_line="여기까지가 오늘 체크할 포인트입니다.",
        catchphrase="핵심만 보면 흐름이 보입니다.",
    ),
    "calm_anchor": PersonaProfile(
        persona_id="calm_anchor",
        name="차분한 앵커",
        role="정리형 경제 진행자",
        tone="차분하고 신뢰감 있는 스타일",
        voice_style="낮고 안정적인 톤, 숫자는 천천히 또렷하게",
        intro_line="오늘 뉴스, 시장에 영향 큰 것부터 차분히 보겠습니다.",
        closing_line="이상으로 오늘의 시장 브리핑이었습니다.",
        catchphrase="숫자보다 중요한 건 방향입니다.",
    ),
    "street_mentor": PersonaProfile(
        persona_id="street_mentor",
        name="주식 선배",
        role="쉽게 풀어주는 경제 선배",
        tone="친근하고 설명이 쉬운 스타일",
        voice_style="말하듯 자연스럽게, 중간중간 쉬어가며 설명",
        intro_line="오늘 뉴스, 어렵게 말하지 않고 쉽게 풀어드릴게요.",
        closing_line="내일도 흔들릴 수 있으니, 포지션은 가볍게 보세요.",
        catchphrase="이건 숫자보다 맥락을 봐야 합니다.",
    ),
}


def _normalize(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", value.lower()).strip()


def _source_weight(source: str) -> int:
    normalized = _normalize(source)
    for key, weight in SOURCE_WEIGHTS.items():
        if key in normalized:
            return weight
    return 1


def _news_signature(item: Dict[str, Any]) -> str:
    title = _normalize(str(item.get("title", "")))
    link = str(item.get("link", "")).strip().lower()
    if link:
        return link
    return re.sub(r"\s+", " ", title)


def _keyword_hits(text: str) -> int:
    normalized = _normalize(text)
    return sum(1 for keyword in MARKET_KEYWORDS if keyword in normalized)


def _recency_bonus(published: str) -> float:
    if not published:
        return 0.0
    try:
        parsed = dt.datetime.fromisoformat(published.replace("Z", "+00:00"))
    except ValueError:
        return 0.0
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=dt.timezone.utc)
    now = dt.datetime.now(dt.timezone.utc)
    age_hours = max(0.0, (now - parsed.astimezone(dt.timezone.utc)).total_seconds() / 3600.0)
    if age_hours <= 6:
        return 3.0
    if age_hours <= 24:
        return 2.0
    if age_hours <= 48:
        return 1.0
    return 0.0


def _news_score(item: Dict[str, Any], index: int) -> float:
    title = str(item.get("title", ""))
    source = str(item.get("source", ""))
    score = 0.0
    score += _source_weight(source)
    score += min(4, _keyword_hits(title))
    if re.search(r"\b(fed|cpi|ppi|jobs|earnings|guidance|tariff|rate|yield|inflation)\b", _normalize(title)):
        score += 2.5
    if any(token in _normalize(title) for token in ["stocks", "market", "bitcoin", "crypto", "semiconductor", "ai"]):
        score += 1.0
    score += _recency_bonus(str(item.get("published", "")))
    score -= min(index, 4) * 0.15
    return score


def _dedupe_news(items: Iterable[Dict[str, Any]]) -> List[Dict[str, Any]]:
    seen = set()
    deduped = []
    for item in items:
        signature = _news_signature(item)
        if signature in seen:
            continue
        seen.add(signature)
        deduped.append(item)
    return deduped


def select_news_items(data: Dict[str, Any], limit: int = 3) -> List[Dict[str, Any]]:
    news = data.get("news", {}).get("items", [])
    scored: List[Tuple[float, Dict[str, Any]]] = []
    for index, item in enumerate(_dedupe_news(news)):
        title = str(item.get("title", "")).strip()
        if not title:
            continue
        scored.append((_news_score(item, index), item))
    scored.sort(key=lambda pair: pair[0], reverse=True)
    selected = [dict(item) for _, item in scored[: max(1, limit)]]
    return selected


def _format_market_snapshot(data: Dict[str, Any]) -> Dict[str, Any]:
    core = _market_core_snapshot(data)
    dashboard = _risk_dashboard(data)
    regime = _regime_label(data, dashboard)
    return {
        "verdict": dashboard.get("verdict", "중립~우호"),
        "score": dashboard.get("score", 0),
        "regime": regime,
        "plain_verdict": _plain_verdict(dashboard),
        "spy_1mo_pct": core.get("spy_1mo_pct"),
        "rsp_1mo_pct": core.get("rsp_1mo_pct"),
        "iwm_1mo_pct": core.get("iwm_1mo_pct"),
        "vix": core.get("vix"),
        "us10y": core.get("us10y"),
        "dxy": core.get("dxy"),
        "usdkrw": core.get("usdkrw"),
        "hy_spread": core.get("hy_spread"),
    }


def _tone_opening(persona: PersonaProfile, market: Dict[str, Any], selected_news: Sequence[Dict[str, Any]]) -> str:
    if selected_news:
        first = selected_news[0]
        headline = str(first.get("title", "")).strip()
    else:
        headline = "오늘은 시장 자체가 주인공입니다"
    if market["score"] >= 5:
        market_line = "지금은 방어적으로 보는 게 맞는 구간입니다."
    elif market["score"] >= 2:
        market_line = "반등은 열려 있지만 확인할 것들이 아직 남아 있습니다."
    else:
        market_line = "큰 흔들림은 제한적이지만 뉴스 반응은 계속 봐야 합니다."
    return f"{persona.intro_line} {market_line} 오늘 가장 먼저 볼 뉴스는 {headline}입니다."


def _build_scenes(persona: PersonaProfile, market: Dict[str, Any], selected_news: Sequence[Dict[str, Any]]) -> List[Dict[str, Any]]:
    scenes: List[Dict[str, Any]] = []
    scenes.append(
        {
            "id": "hook",
            "duration_sec": 6,
            "speaker": persona.name,
            "narration": _tone_opening(persona, market, selected_news),
            "screen_text": market["plain_verdict"],
            "visual": "Character intro card with bold market headline and rising/falling chart line.",
        }
    )

    market_context = (
        f"시장 국면은 {market['regime']}이고, "
        f"VIX {market['vix']}, 10년물 {market['us10y']}, 달러지수 {market['dxy']}를 같이 보면 됩니다."
    )
    scenes.append(
        {
            "id": "market_context",
            "duration_sec": 10,
            "speaker": persona.name,
            "narration": market_context,
            "screen_text": f"{market['regime']} / Risk {market['score']}",
            "visual": "Dashboard-style market summary with VIX, rates, dollar, and KRW.",
        }
    )

    for index, item in enumerate(selected_news[:3], start=1):
        title = str(item.get("title", "")).strip()
        source = str(item.get("source", "뉴스")).strip()
        published = str(item.get("published", "")).strip()
        if published:
            try:
                published_dt = dt.datetime.fromisoformat(published.replace("Z", "+00:00"))
                if published_dt.tzinfo is None:
                    published_dt = published_dt.replace(tzinfo=dt.timezone.utc)
                published = published_dt.astimezone(REPORT_TIMEZONE).strftime("%m-%d %H:%M")
            except ValueError:
                published = published[:16]
        scene_text = (
            f"{index}번째 뉴스는 {title}입니다. "
            f"출처는 {source}이고, {published or '방금 전'} 기준으로 시장 민감도가 높은 소식으로 봐도 됩니다."
        )
        scenes.append(
            {
                "id": f"news_{index}",
                "duration_sec": 12,
                "speaker": persona.name,
                "narration": scene_text,
                "screen_text": title,
                "visual": f"News card with source label {source} and short bullet explanation.",
                "source": source,
                "published": published or None,
                "title": title,
            }
        )

    closing = (
        f"{persona.catchphrase} "
        f"{persona.closing_line} "
        "오늘은 뉴스 제목보다 시장이 왜 반응했는지를 보는 게 핵심입니다."
    )
    scenes.append(
        {
            "id": "closing",
            "duration_sec": 8,
            "speaker": persona.name,
            "narration": closing,
            "screen_text": "내일 체크포인트: 금리, 달러, VIX",
            "visual": "Closing card with three checkpoints and channel branding.",
        }
    )
    return scenes


def _build_title(market: Dict[str, Any], selected_news: Sequence[Dict[str, Any]]) -> str:
    if selected_news:
        headline = str(selected_news[0].get("title", "")).strip()
        headline = re.sub(r"[\[\]\(\)]", "", headline)
        if len(headline) > 42:
            headline = headline[:39].rstrip() + "..."
        return f"오늘 시장 핵심 뉴스 1분 요약 | {headline}"
    return f"오늘 시장 핵심 뉴스 1분 요약 | {market['regime']}"


def _build_description(persona: PersonaProfile, market: Dict[str, Any], selected_news: Sequence[Dict[str, Any]]) -> str:
    bullets = []
    for item in selected_news[:3]:
        bullets.append(f"- {item.get('title', '').strip()}")
    bullets_text = "\n".join(bullets) if bullets else "- 오늘 시장 데이터 기반 요약"
    return (
        f"{persona.role} 스타일로 오늘 시장과 핵심 뉴스를 1분 안팎으로 정리한 쇼츠입니다.\n"
        f"시장 국면: {market['regime']}\n"
        f"{bullets_text}\n\n"
        "이 영상은 자동 생성용 원고와 편집 지시를 함께 담은 매니페스트입니다.\n"
        "실제 업로드 전에는 TTS, 자막, 썸네일, 최종 사실 확인 단계를 거치세요."
    )


def _build_tags(selected_news: Sequence[Dict[str, Any]]) -> List[str]:
    tags = ["경제", "유튜브쇼츠", "시장브리핑", "뉴스요약", "투자참고"]
    for item in selected_news[:3]:
        title = _normalize(str(item.get("title", "")))
        if "fed" in title or "rate" in title or "yield" in title:
            tags.append("금리")
        if "inflation" in title or "cpi" in title:
            tags.append("인플레이션")
        if "bitcoin" in title or "crypto" in title:
            tags.append("비트코인")
        if "ai" in title:
            tags.append("AI")
        if "earnings" in title:
            tags.append("실적")
    return list(dict.fromkeys(tags))


def build_youtube_short_package(
    data: Dict[str, Any],
    persona_id: str = "economy_host",
    news_limit: int = 3,
) -> Dict[str, Any]:
    persona = PERSONA_LIBRARY.get(persona_id, PERSONA_LIBRARY["economy_host"])
    selected_news = select_news_items(data, limit=news_limit)
    market = _format_market_snapshot(data)
    scenes = _build_scenes(persona, market, selected_news)
    tts_text = "\n".join(scene["narration"] for scene in scenes)
    now = dt.datetime.now(REPORT_TIMEZONE)
    package = {
        "project_id": "market-agent",
        "artifact_type": "youtube_shorts_package",
        "generated_at": now.isoformat(timespec="seconds"),
        "persona": asdict(persona),
        "title": _build_title(market, selected_news),
        "description": _build_description(persona, market, selected_news),
        "hashtags": ["#경제", "#뉴스", "#시장브리핑", "#유튜브쇼츠"],
        "tags": _build_tags(selected_news),
        "selected_news": selected_news,
        "market_context": market,
        "script": {
            "style": persona.tone,
            "voice_style": persona.voice_style,
            "hook": scenes[0]["narration"] if scenes else "",
            "scenes": scenes,
            "tts_text": tts_text,
        },
        "render": {
            "aspect_ratio": "9:16",
            "target_duration_sec": sum(scene["duration_sec"] for scene in scenes),
            "subtitles": True,
            "music_mood": "clean newsroom / light modern beat",
            "thumbnail_style": {
                "headline": selected_news[0]["title"][:42] if selected_news else market["regime"],
                "subheadline": market["plain_verdict"],
                "palette": "dark navy, white, yellow accent",
            },
        },
        "upload": {
            "privacy_status": "private",
            "made_for_kids": False,
            "category_id": "25",
            "language": "ko",
        },
    }
    return package


def _render_markdown(package: Dict[str, Any]) -> str:
    persona = package.get("persona", {})
    market = package.get("market_context", {})
    lines = [
        f"# {package.get('title', 'YouTube Shorts Package')}",
        "",
        f"- persona: {persona.get('name', 'n/a')} ({persona.get('persona_id', 'n/a')})",
        f"- role: {persona.get('role', 'n/a')}",
        f"- generated_at: {package.get('generated_at', 'n/a')}",
        f"- market_regime: {market.get('regime', 'n/a')}",
        f"- verdict: {market.get('verdict', 'n/a')} (score {market.get('score', 'n/a')})",
        "",
        "## Hook",
        package.get("script", {}).get("hook", ""),
        "",
        "## Scenes",
    ]
    for scene in package.get("script", {}).get("scenes", []):
        lines.extend(
            [
                f"### {scene.get('id', 'scene')} ({scene.get('duration_sec', 0)}s)",
                f"- narration: {scene.get('narration', '')}",
                f"- screen_text: {scene.get('screen_text', '')}",
                f"- visual: {scene.get('visual', '')}",
                "",
            ]
        )
    lines.extend(
        [
            "## Selected News",
        ]
    )
    for item in package.get("selected_news", []):
        lines.append(f"- {item.get('title', '')} [{item.get('source', '')}]")
    lines.extend(
        [
            "",
            "## TTS",
            package.get("script", {}).get("tts_text", ""),
            "",
            "## Render",
            json.dumps(package.get("render", {}), ensure_ascii=False, indent=2),
        ]
    )
    return "\n".join(lines)


def save_youtube_package(package: Dict[str, Any], shorts_dir: Path = SHORTS_DIR) -> Tuple[Path, Path]:
    shorts_dir.mkdir(parents=True, exist_ok=True)
    generated_at = package.get("generated_at") or dt.datetime.now(REPORT_TIMEZONE).isoformat(timespec="seconds")
    try:
        parsed = dt.datetime.fromisoformat(str(generated_at))
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=REPORT_TIMEZONE)
        stamp = parsed.astimezone(REPORT_TIMEZONE).strftime("%Y-%m-%d_%H%M%S")
    except ValueError:
        stamp = dt.datetime.now(REPORT_TIMEZONE).strftime("%Y-%m-%d_%H%M%S")
    persona_id = str(package.get("persona", {}).get("persona_id", "persona"))
    stem = f"{stamp}_{persona_id}"
    json_path = shorts_dir / f"{stem}.json"
    md_path = shorts_dir / f"{stem}.md"
    json_path.write_text(json.dumps(package, ensure_ascii=False, indent=2), encoding="utf-8")
    md_path.write_text(_render_markdown(package) + "\n", encoding="utf-8")
    (shorts_dir / "latest.json").write_text(json.dumps(package, ensure_ascii=False, indent=2), encoding="utf-8")
    (shorts_dir / "latest.md").write_text(_render_markdown(package) + "\n", encoding="utf-8")
    return md_path, json_path
