import datetime as dt
import json
import re
from typing import Any, Dict, Iterable, List, Optional

from .config import REPORT_TIMEZONE
from .http import post_json


def _fmt_num(value: Any, digits: int = 2) -> str:
    if value is None:
        return "n/a"
    try:
        return f"{float(value):,.{digits}f}"
    except (TypeError, ValueError):
        return str(value)


def _fmt_pct(value: Any) -> str:
    if value is None:
        return "n/a"
    try:
        sign = "+" if float(value) > 0 else ""
        return f"{sign}{float(value):.2f}%"
    except (TypeError, ValueError):
        return str(value)


def _as_float(value: Any) -> Optional[float]:
    try:
        if value is None:
            return None
        return float(str(value).replace(",", "").replace("%", ""))
    except (TypeError, ValueError):
        return None


def _row_map(rows: Iterable[Dict[str, Any]]) -> Dict[str, Dict[str, Any]]:
    return {row.get("label"): row for row in rows}


def _market_value(data: Dict[str, Any], region: str, label: str) -> Optional[float]:
    for row in data.get("markets", {}).get(region, []):
        if row.get("label") == label:
            try:
                return float(row.get("price"))
            except (TypeError, ValueError):
                return None
    return None


def _quote_lines(rows: Iterable[Dict[str, Any]]) -> List[str]:
    lines = []
    for row in rows:
        if row.get("error"):
            lines.append(f"- {row['label']}: 수집 실패 ({row['error']})")
            continue
        lines.append(
            f"- {row['label']}: {_fmt_num(row.get('price'))} "
            f"({_fmt_pct(row.get('change_pct'))})"
        )
    return lines


def _internal_lines(data: Dict[str, Any]) -> List[str]:
    rows = _row_map(data.get("internals", {}).get("items", []))

    def pct(label: str) -> Optional[float]:
        return rows.get(label, {}).get("change_pct")

    spy = pct("S&P 500 ETF")
    rsp = pct("Equal Weight S&P 500")
    iwm = pct("Small Caps ETF")
    hyg = pct("High Yield Bond ETF")
    lqd = pct("Investment Grade Bond ETF")
    kre = pct("Regional Banks ETF")
    soxx = pct("Semiconductors ETF")
    tlt = pct("Long Treasury ETF")
    gold = pct("Gold ETF")
    dollar = pct("Dollar Bullish ETF")
    lines = [
        f"- 시장 폭: Equal Weight S&P 500 {_fmt_pct(rsp)} vs S&P 500 ETF {_fmt_pct(spy)}",
        f"- 위험선호: Small Caps {_fmt_pct(iwm)}, Semiconductors {_fmt_pct(soxx)}, Regional Banks {_fmt_pct(kre)}",
        f"- 크레딧/방어: High Yield {_fmt_pct(hyg)} vs Investment Grade {_fmt_pct(lqd)}",
        f"- 안전자산: Long Treasury {_fmt_pct(tlt)}, Gold {_fmt_pct(gold)}, Dollar {_fmt_pct(dollar)}",
    ]
    return lines


def _macro_lines(macro: Dict[str, Any]) -> List[str]:
    lines = []
    for series_id in ["DGS10", "DGS2", "T10Y2Y", "T10Y3M", "BAMLH0A0HYM2", "DCOILWTICO"]:
        row = macro.get(series_id, {})
        if row.get("error"):
            lines.append(f"- {row.get('label', series_id)}: 수집 실패 ({row['error']})")
            continue
        suffix = "%" if series_id != "DCOILWTICO" else "달러"
        source = row.get("source", "FRED")
        date = row.get("date") or "latest"
        lines.append(
            f"- {row.get('label', series_id)}: {_fmt_num(row.get('value'))}{suffix} "
            f"({date}, {source})"
        )
    return lines


def _valuation_lines(valuation: Dict[str, Any]) -> List[str]:
    lines = []
    for key in ["shiller_pe", "sp500_pe", "earnings_yield", "current_market_valuation"]:
        row = valuation.get(key, {})
        if row.get("error"):
            lines.append(f"- {row.get('label', key)}: 수집 실패 ({row['error']})")
            continue
        value = row.get("value") or "snapshot"
        desc = row.get("description") or ""
        if len(desc) > 150:
            desc = desc[:147].rstrip() + "..."
        lines.append(f"- {row.get('label', key)}: {value} ({row.get('url', 'n/a')})")
        if desc:
            lines.append(f"  근거: {desc}")
    return lines or ["- 밸류에이션 데이터 수집 실패"]


def _news_lines(news: Dict[str, Any]) -> List[str]:
    items = news.get("items", [])[:6]
    if not items:
        return ["- 최신 뉴스 RSS 수집 실패 또는 관련 헤드라인 없음"]
    lines = []
    for item in items:
        source = item.get("source", "news")
        published = item.get("published") or "n/a"
        if "T" in published:
            published = published.replace("+00:00", "Z")
        lines.append(f"- [{source}] {item.get('title')} ({published})")
    return lines


def _trend_row_map(trend: Dict[str, Any]) -> Dict[str, Dict[str, Dict[str, Any]]]:
    items = trend.get("items", {})
    return {period: _row_map(rows) for period, rows in items.items()}


def _trend_lines(data: Dict[str, Any]) -> List[str]:
    trend = _trend_row_map(data.get("trend", {}))
    one = trend.get("1mo", {})
    three = trend.get("3mo", {})

    def row(period: str, label: str) -> Optional[float]:
        return trend.get(period, {}).get(label, {}).get("change_pct")

    return [
        (
            f"- 1개월 추세: SPY {_fmt_pct(row('1mo', 'S&P 500 ETF'))}, "
            f"RSP {_fmt_pct(row('1mo', 'Equal Weight S&P 500'))}, "
            f"IWM {_fmt_pct(row('1mo', 'Small Caps ETF'))}, "
            f"HYG {_fmt_pct(row('1mo', 'High Yield Bond ETF'))}, "
            f"LQD {_fmt_pct(row('1mo', 'Investment Grade Bond ETF'))}"
        ),
        (
            f"- 3개월 추세: SPY {_fmt_pct(row('3mo', 'S&P 500 ETF'))}, "
            f"RSP {_fmt_pct(row('3mo', 'Equal Weight S&P 500'))}, "
            f"IWM {_fmt_pct(row('3mo', 'Small Caps ETF'))}, "
            f"SOXX {_fmt_pct(row('3mo', 'Semiconductors ETF'))}, "
            f"TLT {_fmt_pct(row('3mo', 'Long Treasury ETF'))}"
        ),
    ]


def _ai_verdict_bias(verdict: str) -> float:
    mapping = {
        "위험 우위": 1.0,
        "주의": 0.5,
        "중립~우호": -0.5,
    }
    return mapping.get(verdict, 0.0)


def _parse_ai_signal(text: str) -> Dict[str, Any]:
    try:
        signal = json.loads(text)
        if isinstance(signal, dict):
            return signal
    except Exception:
        pass
    match = re.search(r"\{[\s\S]*\}", text)
    if match:
        try:
            signal = json.loads(match.group(0))
            if isinstance(signal, dict):
                return signal
        except Exception:
            pass
    return {}


def build_ai_signal(data: Dict[str, Any], api_key: str, model: str, dashboard: Optional[Dict[str, Any]] = None) -> Optional[Dict[str, Any]]:
    dashboard = dashboard or _risk_dashboard(data)
    prompt = (
        "너는 시장 분석 보조 엔진이다. "
        "반드시 JSON 객체만 출력해라. "
        "키는 verdict, confidence, summary, key_factors, missing_data, action, trade_mode, position_size, entry_condition, invalidation 으로 제한해라. "
        "verdict는 '위험 우위', '주의', '중립~우호' 중 하나만 써라. "
        "confidence는 0~100 정수로 써라. "
        "summary는 2문장 이내 한국어로 써라. "
        "key_factors는 최대 4개 문자열 배열, missing_data는 최대 4개 문자열 배열, action은 한 문장으로 써라. "
        "trade_mode는 '실전 후보', '조건부', '관망', '현금 우선' 중 하나로 써라. "
        "position_size는 0~100 정수로 써라. "
        "entry_condition과 invalidation은 각각 한 문장으로 써라. "
        "아래 JSON의 rule_dashboard와 raw_data를 함께 보고, 룰과 다를 수 있으면 왜 다른지 짧게 반영해라. "
        "확정적 예측 대신 조건부 판단을 써라."
    )
    payload = {
        "model": model,
        "input": [
            {"role": "system", "content": prompt},
            {
                "role": "user",
                "content": json.dumps({"rule_dashboard": dashboard, "raw_data": data}, ensure_ascii=False)[:50000],
            },
        ],
    }
    try:
        response = post_json("https://api.openai.com/v1/responses", payload, bearer=api_key, timeout=60)
        chunks = []
        for item in response.get("output", []):
            for content in item.get("content", []):
                if content.get("type") == "output_text":
                    chunks.append(content.get("text", ""))
        text = "\n".join(chunk for chunk in chunks if chunk).strip()
        signal = _parse_ai_signal(text)
        if not signal:
            return None
    except Exception as exc:
        if not _openai_failed_softly(exc):
            return None
        return None
    verdict = signal.get("verdict") or "중립~우호"
    if verdict not in {"위험 우위", "주의", "중립~우호"}:
        verdict = "중립~우호"
    try:
        confidence = int(signal.get("confidence", 50))
    except (TypeError, ValueError):
        confidence = 50
    signal["verdict"] = verdict
    signal["confidence"] = max(0, min(100, confidence))
    signal["summary"] = str(signal.get("summary", "")).strip()
    signal["action"] = str(signal.get("action", "")).strip()
    signal["key_factors"] = signal.get("key_factors", []) if isinstance(signal.get("key_factors"), list) else []
    signal["missing_data"] = signal.get("missing_data", []) if isinstance(signal.get("missing_data"), list) else []
    signal["trade_mode"] = str(signal.get("trade_mode", "")).strip()
    try:
        position_size = int(signal.get("position_size", 0))
    except (TypeError, ValueError):
        position_size = 0
    signal["position_size"] = max(0, min(100, position_size))
    signal["entry_condition"] = str(signal.get("entry_condition", "")).strip()
    signal["invalidation"] = str(signal.get("invalidation", "")).strip()
    return signal


def _combine_verdicts(rule_dashboard: Dict[str, Any], ai_signal: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    rule_score = float(rule_dashboard.get("score", 0) or 0)
    combined_score = rule_score
    ai_verdict = "중립~우호"
    ai_confidence = 0
    if ai_signal:
        ai_verdict = ai_signal.get("verdict") or "중립~우호"
        try:
            ai_confidence = int(ai_signal.get("confidence", 0))
        except (TypeError, ValueError):
            ai_confidence = 0
        ai_confidence = max(0, min(100, ai_confidence))
        combined_score += _ai_verdict_bias(ai_verdict) * (ai_confidence / 100.0) * 2.0

    if combined_score >= 6:
        verdict = "위험 우위"
    elif combined_score >= 3:
        verdict = "주의"
    else:
        verdict = "중립~우호"
    return {
        "verdict": verdict,
        "score": combined_score,
        "rule_verdict": rule_dashboard.get("verdict", "중립~우호"),
        "rule_score": rule_score,
        "ai_verdict": ai_verdict,
        "ai_confidence": ai_confidence,
    }


def _risk_dashboard(data: Dict[str, Any]) -> Dict[str, Any]:
    markets = data.get("markets", {})
    us = _row_map(markets.get("US", []))
    internals = _row_map(data.get("internals", {}).get("items", []))
    trend = _trend_row_map(data.get("trend", {}))
    macro = data.get("macro", {})
    valuation = data.get("valuation", {})
    fear_greed = data.get("fear_greed", {})
    fg_data = fear_greed.get("data", {}) if fear_greed.get("ok") else {}

    def ipct(label: str) -> Optional[float]:
        return internals.get(label, {}).get("change_pct")

    def upct(label: str) -> Optional[float]:
        return us.get(label, {}).get("change_pct")

    def tpc(period: str, label: str) -> Optional[float]:
        return trend.get(period, {}).get(label, {}).get("change_pct")

    score = 0
    positives: List[str] = []
    negatives: List[str] = []
    conditions: List[str] = []

    vix = us.get("VIX", {}).get("price")
    tnx = us.get("US 10Y Yield", {}).get("price")
    dollar = upct("Dollar Index")
    spy = ipct("S&P 500 ETF")
    rsp = ipct("Equal Weight S&P 500")
    iwm = ipct("Small Caps ETF")
    hyg = ipct("High Yield Bond ETF")
    lqd = ipct("Investment Grade Bond ETF")
    kre = ipct("Regional Banks ETF")
    tlt = ipct("Long Treasury ETF")
    cape = _as_float(valuation.get("shiller_pe", {}).get("value"))
    pe = _as_float(valuation.get("sp500_pe", {}).get("value"))
    earnings_yield = _as_float(valuation.get("earnings_yield", {}).get("value"))
    us_fg = (fg_data.get("us") or {}).get("score")

    if isinstance(vix, (int, float)):
        if vix >= 25:
            score += 2
            negatives.append(f"VIX {vix:.1f}: 변동성 스트레스가 높은 구간")
        elif vix >= 20:
            score += 1
            negatives.append(f"VIX {vix:.1f}: 변동성이 평시보다 높음")
        elif vix <= 14:
            score -= 1
            positives.append(f"VIX {vix:.1f}: 단기 변동성 압력은 낮음")

    if isinstance(spy, (int, float)):
        if spy <= -1:
            score += 1
            negatives.append(f"SPY {_fmt_pct(spy)}: 지수 자체 모멘텀이 약함")
        elif spy >= 1:
            score -= 1
            positives.append(f"SPY {_fmt_pct(spy)}: 지수 모멘텀은 우호적")

    spy_1m = tpc("1mo", "S&P 500 ETF")
    rsp_1m = tpc("1mo", "Equal Weight S&P 500")
    iwm_1m = tpc("1mo", "Small Caps ETF")
    hyg_1m = tpc("1mo", "High Yield Bond ETF")
    lqd_1m = tpc("1mo", "Investment Grade Bond ETF")
    tlt_1m = tpc("1mo", "Long Treasury ETF")
    vix_1m = tpc("1mo", "VIX")

    if isinstance(spy_1m, (int, float)):
        if spy_1m <= -3:
            score += 1
            negatives.append(f"SPY 1개월 {_fmt_pct(spy_1m)}: 중기 추세가 약함")
        elif spy_1m >= 3:
            score -= 1
            positives.append(f"SPY 1개월 {_fmt_pct(spy_1m)}: 중기 추세가 우호적")

    if isinstance(rsp_1m, (int, float)) and isinstance(spy_1m, (int, float)):
        breadth_trend = rsp_1m - spy_1m
        if breadth_trend < -1:
            score += 1
            negatives.append(f"RSP 1개월이 SPY보다 {abs(breadth_trend):.2f}%p 약해 시장 폭이 좁음")
        elif breadth_trend > 1:
            score -= 1
            positives.append(f"RSP 1개월이 SPY보다 {breadth_trend:.2f}%p 강해 시장 폭이 개선")

    if isinstance(iwm_1m, (int, float)) and isinstance(spy_1m, (int, float)) and iwm_1m - spy_1m < -1.5:
        score += 1
        negatives.append("소형주 1개월 추세가 대형주보다 약해 위험선호 확장이 약함")

    if isinstance(hyg_1m, (int, float)) and isinstance(lqd_1m, (int, float)):
        credit_trend = hyg_1m - lqd_1m
        if credit_trend < -1:
            score += 1
            negatives.append("1개월 기준 하이일드가 투자등급보다 약해 크레딧 선호가 둔화")
        elif credit_trend > 1:
            score -= 1
            positives.append("1개월 기준 하이일드가 투자등급보다 강해 크레딧 선호는 유지")

    if isinstance(vix_1m, (int, float)):
        if vix_1m >= 8:
            score += 1
            negatives.append(f"VIX 1개월 {_fmt_pct(vix_1m)}: 변동성 체감이 높아짐")
        elif vix_1m <= -8:
            score -= 1
            positives.append(f"VIX 1개월 {_fmt_pct(vix_1m)}: 변동성 압력이 완화")

    if isinstance(tlt_1m, (int, float)) and isinstance(spy_1m, (int, float)) and tlt_1m < 0 and spy_1m < 0:
        score += 1
        negatives.append("장기채와 주식이 같이 약해져 위험자산 회복력이 떨어짐")

    if isinstance(spy, (int, float)) and isinstance(rsp, (int, float)):
        breadth_gap = rsp - spy
        if breadth_gap < -0.5:
            score += 1
            negatives.append(f"동일가중이 시총가중보다 {abs(breadth_gap):.2f}%p 약해 상승 폭이 좁음")
        elif breadth_gap > 0.5:
            score -= 1
            positives.append(f"동일가중이 시총가중보다 {breadth_gap:.2f}%p 강해 시장 폭이 개선")

    if isinstance(spy, (int, float)) and isinstance(iwm, (int, float)) and iwm - spy < -0.8:
        score += 1
        negatives.append("소형주가 대형주보다 뚜렷하게 약해 위험선호가 약함")

    if isinstance(hyg, (int, float)) and isinstance(lqd, (int, float)):
        credit_gap = hyg - lqd
        if credit_gap < -0.3:
            score += 1
            negatives.append(f"하이일드가 투자등급보다 {abs(credit_gap):.2f}%p 약해 크레딧 선호가 둔화")
        elif credit_gap > 0.3:
            score -= 1
            positives.append(f"하이일드가 투자등급보다 {credit_gap:.2f}%p 강해 크레딧 선호는 유지")

    if isinstance(kre, (int, float)) and kre <= -1:
        score += 1
        negatives.append(f"지역은행 ETF {_fmt_pct(kre)}: 금융 스트레스 민감 섹터 약세")

    if isinstance(tnx, (int, float)) and tnx >= 4.5:
        score += 1
        negatives.append(f"미국 10년물 {tnx:.2f}%: 밸류에이션 할인율 부담")

    if isinstance(tlt, (int, float)) and isinstance(tnx, (int, float)) and tlt < 0 and tnx >= 4.5:
        score += 1
        negatives.append("장기채 약세와 높은 금리가 동시에 나타나 성장주 할인율 부담")

    if isinstance(dollar, (int, float)) and dollar > 0.4:
        score += 1
        negatives.append(f"달러지수 {_fmt_pct(dollar)}: 비미국 위험자산에 부담")

    if isinstance(cape, (int, float)) and cape >= 35:
        score += 2
        negatives.append(f"Shiller CAPE {cape:.1f}: 장기 밸류에이션 부담이 높은 구간")
    elif isinstance(pe, (int, float)) and pe >= 28:
        score += 1
        negatives.append(f"S&P 500 P/E {pe:.1f}: 이익 대비 가격 부담이 큰 편")

    dgs10 = macro.get("DGS10", {}).get("value")
    if isinstance(earnings_yield, (int, float)) and isinstance(dgs10, (int, float)) and earnings_yield < dgs10:
        score += 1
        negatives.append(f"주식 이익수익률 {earnings_yield:.2f}%가 10년물 {dgs10:.2f}%보다 낮아 위험보상 매력이 약함")

    if isinstance(us_fg, int):
        if us_fg <= 25:
            score += 1
            positives.append("미국 Fear & Greed가 극단 공포권이면 반등 여지는 생기지만 변동성도 큼")
        elif us_fg >= 75:
            score += 1
            negatives.append("미국 Fear & Greed가 탐욕권이면 추격 매수 리스크가 커짐")

    if score >= 6:
        verdict = "위험 우위"
        stance = "현재는 추가 상승보다 하방 변동성 관리가 우선입니다."
    elif score >= 3:
        verdict = "주의"
        stance = "반등은 가능하지만 폭이 좁거나 금리/크레딧 부담이 남아 확인 매수가 유리합니다."
    else:
        verdict = "중립~우호"
        stance = "위험 신호가 과도하지 않다면 단기 상승 시도는 가능하나 뉴스와 금리 확인이 필요합니다."

    conditions.append("상승 지속 조건: 동일가중/소형주/하이일드가 지수보다 강해지고 VIX가 낮아져야 합니다.")
    conditions.append("하방 확대 조건: VIX 25 상회, 하이일드 약세, 달러/금리 동반 상승이 겹치면 방어적으로 봅니다.")

    return {
        "score": score,
        "verdict": verdict,
        "stance": stance,
        "positives": positives[:4],
        "negatives": negatives[:8],
        "conditions": conditions,
    }


def _dashboard_lines(data: Dict[str, Any]) -> List[str]:
    dashboard = _risk_dashboard(data)
    lines = [
        f"- 종합 판단: {dashboard['verdict']} (risk score {dashboard['score']})",
        f"- 해석: {dashboard['stance']}",
    ]
    if dashboard["negatives"]:
        lines.append("- 부담 요인:")
        lines.extend(f"  - {item}" for item in dashboard["negatives"])
    if dashboard["positives"]:
        lines.append("- 완충/반등 요인:")
        lines.extend(f"  - {item}" for item in dashboard["positives"])
    lines.append("- 조건부 시나리오:")
    lines.extend(f"  - {item}" for item in dashboard["conditions"])
    return lines


def _market_snapshot_lines(data: Dict[str, Any]) -> List[str]:
    markets = data.get("markets", {})
    us = _row_map(markets.get("US", []))
    korea = _row_map(markets.get("Korea", []))
    china = _row_map(markets.get("China", []))
    internals = _row_map(data.get("internals", {}).get("items", []))
    macro = data.get("macro", {})

    return [
        (
            f"- 미국: S&P500 {_fmt_pct(us.get('S&P 500', {}).get('change_pct'))}, "
            f"Nasdaq {_fmt_pct(us.get('Nasdaq', {}).get('change_pct'))}, "
            f"VIX {_fmt_num(us.get('VIX', {}).get('price'), 1)}"
        ),
        (
            f"- 한국: KOSPI {_fmt_pct(korea.get('KOSPI', {}).get('change_pct'))}, "
            f"KOSDAQ {_fmt_pct(korea.get('KOSDAQ', {}).get('change_pct'))}, "
            f"USD/KRW {_fmt_num(korea.get('USD/KRW', {}).get('price'), 1)}"
        ),
        (
            f"- 중국/홍콩: Shanghai {_fmt_pct(china.get('Shanghai Composite', {}).get('change_pct'))}, "
            f"Hang Seng {_fmt_pct(china.get('Hang Seng', {}).get('change_pct'))}"
        ),
        (
            f"- 내부: RSP {_fmt_pct(internals.get('Equal Weight S&P 500', {}).get('change_pct'))}, "
            f"IWM {_fmt_pct(internals.get('Small Caps ETF', {}).get('change_pct'))}, "
            f"HYG {_fmt_pct(internals.get('High Yield Bond ETF', {}).get('change_pct'))}"
        ),
        (
            f"- 금리/원자재: 미 10Y {_fmt_num(macro.get('DGS10', {}).get('value'))}%, "
            f"2Y {_fmt_num(macro.get('DGS2', {}).get('value'))}%, "
            f"WTI {_fmt_num(macro.get('DCOILWTICO', {}).get('value'))}달러"
        ),
    ]


def _valuation_snapshot_line(data: Dict[str, Any]) -> str:
    valuation = data.get("valuation", {})
    macro = data.get("macro", {})
    fear_greed = data.get("fear_greed", {})
    fg_data = fear_greed.get("data", {}) if fear_greed.get("ok") else {}
    us_fg = fg_data.get("us") or {}
    kr_fg = fg_data.get("kr") or {}
    cape = valuation.get("shiller_pe", {}).get("value", "n/a")
    pe = valuation.get("sp500_pe", {}).get("value", "n/a")
    earnings_yield = valuation.get("earnings_yield", {}).get("value", "n/a")
    dgs10 = _fmt_num(macro.get("DGS10", {}).get("value"))
    return (
        f"- CAPE {cape}, S&P500 P/E {pe}, 이익수익률 {earnings_yield} vs 미 10Y {dgs10}%. "
        f"Fear&Greed: 미국 {us_fg.get('score', 'n/a')}({us_fg.get('label', 'n/a')}), "
        f"한국 {kr_fg.get('score', 'n/a')}({kr_fg.get('label', 'n/a')})"
    )


def _compact_news_lines(news: Dict[str, Any], limit: int = 3) -> List[str]:
    lines = []
    seen = set()
    for item in news.get("items", []):
        text = _news_title_ko(item.get("title", ""))
        if text in seen:
            continue
        seen.add(text)
        lines.append(f"- {text}")
        if len(lines) >= limit:
            break
    if not lines:
        return ["- 관련 뉴스 수집 실패 또는 핵심 헤드라인 없음"]
    return lines


def _news_title_ko(title: str) -> str:
    lower = title.lower()
    if "inflation" in lower or "cpi" in lower:
        return "미국 물가/CPI가 금리 경로의 핵심 변수로 부각"
    if "oil" in lower and ("treasury" in lower or "yield" in lower):
        return "유가와 미 국채금리 동반 상승, 인플레·지정학 부담 확대"
    if "iran" in lower:
        return "이란 관련 지정학 리스크가 원유와 금리 변동성을 자극"
    if "ai" in lower and "cheap" in lower:
        return "AI 테마는 유지되지만 저평가/선별 장세 성격이 강해짐"
    if "super micro" in lower or "plunges" in lower:
        return "AI 인프라 개별주 급락으로 기술주 심리 약화"
    if "tech stocks" in lower or "stock plunges" in lower:
        return "기술주 중심 투자심리 약화 신호"
    return title


def _select_core_evidence(dashboard: Dict[str, Any]) -> List[str]:
    negatives = dashboard.get("negatives", [])
    priority_words = [
        "SPY",
        "VIX",
        "10년물",
        "이익수익률",
        "CAPE",
        "장기채",
        "하이일드",
        "달러",
    ]
    selected: List[str] = []
    for word in priority_words:
        for item in negatives:
            if word in item and item not in selected:
                selected.append(item)
                break
        if len(selected) >= 4:
            break
    for item in negatives:
        if item not in selected:
            selected.append(item)
        if len(selected) >= 4:
            break
    return selected


def _plain_reason(item: str) -> str:
    if item.startswith("SPY"):
        return f"{item} → 미국 대표 ETF가 크게 밀려서 단기 분위기는 약합니다."
    if item.startswith("VIX"):
        return f"{item} → 시장이 평소보다 더 불안해하고 있다는 뜻입니다."
    if "10년물" in item and "할인율" in item:
        return f"{item} → 금리가 높으면 성장주/기술주 가격이 눌리기 쉽습니다."
    if "이익수익률" in item:
        return f"{item} → 주식이 채권보다 압도적으로 매력적이라고 보기 어렵습니다."
    if "CAPE" in item:
        return f"{item} → 장기 기준으로 미국 주식 가격 부담이 큽니다."
    if "동일가중" in item:
        return f"{item} → 대형주만 무너진 것이 아니라 일부 종목군은 버티고 있습니다."
    return item


def _confidence_label(data: Dict[str, Any], dashboard: Dict[str, Any]) -> str:
    has_valuation = bool(data.get("valuation", {}).get("shiller_pe", {}).get("ok"))
    has_news = bool(data.get("news", {}).get("items"))
    has_internals = bool(data.get("internals", {}).get("items"))
    has_macro = bool(data.get("macro", {}).get("DGS10", {}).get("value"))
    coverage = sum([has_valuation, has_news, has_internals, has_macro])
    if coverage >= 4 and abs(dashboard.get("score", 0)) >= 3:
        return "높음"
    if coverage >= 3:
        return "보통"
    return "낮음"


def _parse_any_datetime(value: Any) -> Optional[dt.datetime]:
    if value is None:
        return None
    if isinstance(value, (int, float)):
        try:
            return dt.datetime.fromtimestamp(float(value), tz=dt.timezone.utc)
        except (OverflowError, OSError, ValueError):
            return None
    if isinstance(value, str):
        try:
            parsed = dt.datetime.fromisoformat(value)
            if parsed.tzinfo is None:
                parsed = parsed.replace(tzinfo=REPORT_TIMEZONE)
            return parsed.astimezone(dt.timezone.utc)
        except ValueError:
            try:
                return dt.datetime.strptime(value, "%Y-%m-%d").replace(tzinfo=dt.timezone.utc)
            except ValueError:
                return None
    return None


def _freshness_profile(data: Dict[str, Any]) -> Dict[str, Any]:
    now = dt.datetime.now(dt.timezone.utc)
    market_ages: List[float] = []
    for rows in data.get("markets", {}).values():
        for row in rows:
            parsed = _parse_any_datetime(row.get("market_time") or row.get("last_updated"))
            if parsed is not None:
                market_ages.append((now - parsed).total_seconds() / 3600.0)
    news_ages: List[float] = []
    for item in data.get("news", {}).get("items", []):
        parsed = _parse_any_datetime(item.get("published"))
        if parsed is not None:
            news_ages.append((now - parsed).total_seconds() / 3600.0)
    latest_market_age = min(market_ages) if market_ages else None
    latest_news_age = min(news_ages) if news_ages else None
    market_stale = latest_market_age is not None and latest_market_age > 48
    news_stale = latest_news_age is not None and latest_news_age > 72
    market_fresh = latest_market_age is not None and not market_stale
    news_fresh = latest_news_age is not None and not news_stale
    stale = market_stale or news_stale
    score = 0
    if market_fresh:
        score += 70
    if news_fresh:
        score += 30
    missing = []
    if market_stale:
        missing.append("market freshness")
    if news_stale:
        missing.append("news freshness")
    return {
        "score": score,
        "market_age_hours": latest_market_age,
        "news_age_hours": latest_news_age,
        "stale": stale,
        "missing": missing,
    }


def _data_quality_profile(data: Dict[str, Any]) -> Dict[str, Any]:
    freshness = _freshness_profile(data)
    checks = {
        "us_quotes": bool(data.get("markets", {}).get("US")),
        "korea_quotes": bool(data.get("markets", {}).get("Korea")),
        "china_quotes": bool(data.get("markets", {}).get("China")),
        "internals": bool(data.get("internals", {}).get("items")),
        "trend": bool(data.get("trend", {}).get("items", {}).get("1mo")) and bool(data.get("trend", {}).get("items", {}).get("3mo")),
        "macro": any(
            isinstance(row, dict) and row.get("value") is not None
            for row in (data.get("macro", {}) or {}).values()
        ),
        "valuation": any(
            isinstance(row, dict) and row.get("value") is not None
            for row in (data.get("valuation", {}) or {}).values()
        ),
        "news": bool(data.get("news", {}).get("items")),
        "fear_greed": bool(data.get("fear_greed", {}).get("ok")),
        "freshness": freshness["score"] >= 70,
    }
    critical = ["us_quotes", "internals", "macro", "valuation"]
    support = ["korea_quotes", "china_quotes", "trend", "news", "fear_greed"]
    critical_hits = sum(1 for key in critical if checks[key])
    support_hits = sum(1 for key in support if checks[key])
    score = int(round((((critical_hits * 2) + support_hits) / ((len(critical) * 2) + len(support))) * 100))
    missing = [key.replace("_", " ") for key, ok in checks.items() if not ok]
    return {
        "score": score,
        "checks": checks,
        "critical_missing": [key for key in critical if not checks[key]],
        "missing": missing,
        "freshness": freshness,
    }


def _verdict_rank(verdict: str) -> int:
    mapping = {"중립~우호": 0, "주의": 1, "위험 우위": 2}
    return mapping.get(verdict, 0)


def _trade_engine(data: Dict[str, Any], dashboard: Dict[str, Any], ai_signal: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    quality = _data_quality_profile(data)
    calibration = _calibration_profile()
    reliability = _reliability_profile(calibration)
    score = float(dashboard.get("score", 0) or 0)
    verdict = dashboard.get("verdict", "중립~우호")
    evidence_count = len(dashboard.get("positives", [])) + len(dashboard.get("negatives", []))
    base_confidence = quality["score"] * 0.42 + min(abs(score) * 7.5, 24.0) + min(evidence_count * 2.5, 15.0)
    ai_alignment = "n/a"
    if ai_signal:
        ai_verdict = ai_signal.get("verdict") or "중립~우호"
        ai_confidence = int(ai_signal.get("confidence", 0) or 0)
        if _verdict_rank(ai_verdict) == _verdict_rank(verdict):
            base_confidence += 8
            ai_alignment = "일치"
        elif abs(_verdict_rank(ai_verdict) - _verdict_rank(verdict)) == 1:
            base_confidence -= 6
            ai_alignment = "부분 충돌"
        else:
            base_confidence -= 14
            ai_alignment = "강한 충돌"
        if ai_confidence >= 75:
            base_confidence += 3
        elif ai_confidence <= 40:
            base_confidence -= 3
    base_confidence += float(reliability.get("confidence_delta", 0) or 0)
    freshness = quality.get("freshness", {})
    if quality["critical_missing"] or quality["freshness"].get("stale", False):
        trade_mode = "관망"
        position_size = 0
    elif quality["score"] < 55:
        trade_mode = "관망"
        position_size = 0
    elif verdict == "위험 우위":
        trade_mode = "현금 우선"
        position_size = 0
    elif quality["score"] >= 75 and verdict == "중립~우호" and base_confidence >= 72:
        trade_mode = "실전 후보"
        position_size = 100
    elif base_confidence >= 60:
        trade_mode = "조건부"
        position_size = 50 if verdict == "주의" else 75
    else:
        trade_mode = "관망"
        position_size = 25 if verdict == "중립~우호" else 0

    reliability_state = reliability.get("state", "미검증")
    if reliability_state == "불안정":
        trade_mode = "관망"
        position_size = 0
    elif reliability_state == "주의":
        if trade_mode == "실전 후보":
            trade_mode = "조건부"
        position_size = int(round(position_size * float(reliability.get("position_multiplier_cap", 1.0) or 1.0)))
    elif reliability_state == "안정":
        position_size = int(round(position_size * float(reliability.get("position_multiplier_cap", 1.0) or 1.0)))
    position_size = max(0, min(100, position_size))

    if verdict == "위험 우위":
        entry = "추세 확인 전까지 신규 진입을 멈추고 현금/헤지 비중을 유지"
        invalidation = "VIX가 낮아지기 전까지, 또는 RSP/IWM/HYG가 SPY를 다시 이기기 전까지는 진입 보류"
    elif verdict == "주의":
        entry = "SPY와 동일가중, 소형주, 하이일드가 동시에 버티는지 확인한 뒤 분할 검토"
        invalidation = "VIX 재상승이나 달러/금리 동반 강세가 나오면 분할을 중단"
    else:
        entry = "데이터 품질이 높고 시장 폭이 넓을 때만 작은 비중으로 분할 접근"
        invalidation = "시장 폭이 좁아지거나 금리/달러가 다시 부담으로 바뀌면 비중 축소"
    if trade_mode == "실전 후보":
        daily_loss_limit_pct = 1.5
    elif trade_mode == "조건부":
        daily_loss_limit_pct = 1.0
    elif trade_mode == "현금 우선":
        daily_loss_limit_pct = 0.5
    else:
        daily_loss_limit_pct = 0.0

    confidence = int(max(0, min(100, round(base_confidence))))
    return {
        "quality_score": quality["score"],
        "quality_missing": quality["missing"],
        "critical_missing": quality["critical_missing"],
        "freshness": freshness,
        "confidence_score": confidence,
        "trade_mode": trade_mode,
        "position_size": position_size,
        "daily_loss_limit_pct": daily_loss_limit_pct,
        "leverage_allowed": False,
        "calibration_state": reliability_state,
        "calibration_sample_count": int(reliability.get("sample_count", 0) or 0),
        "calibration_note": reliability.get("note", ""),
        "calibration_position_cap": float(reliability.get("position_multiplier_cap", 1.0) or 1.0),
        "entry_condition": entry,
        "invalidation": invalidation,
        "ai_alignment": ai_alignment,
    }


def _regime_label(data: Dict[str, Any], dashboard: Optional[Dict[str, Any]] = None) -> str:
    dashboard = dashboard or _risk_dashboard(data)
    score = float(dashboard.get("score", 0) or 0)
    vix = _market_value(data, "US", "VIX")
    spy_1mo = None
    for row in data.get("trend", {}).get("items", {}).get("1mo", []):
        if row.get("label") == "S&P 500 ETF":
            try:
                spy_1mo = float(row.get("change_pct"))
            except (TypeError, ValueError):
                spy_1mo = None
            break
    if isinstance(vix, (int, float)) and vix >= 25:
        return "고변동성/방어"
    if score >= 5:
        return "방어 우위"
    if isinstance(spy_1mo, (int, float)) and spy_1mo >= 3 and score <= 1:
        return "상승 우호"
    if score <= 1:
        return "횡보/중립"
    return "주의/혼조"


def _final_checkpoints(data: Dict[str, Any]) -> List[str]:
    dashboard = _risk_dashboard(data)
    positives = dashboard.get("positives", [])
    lines = ["- 지금은 새로 크게 사기보다, 이미 가진 포지션의 비중과 손실 가능성을 먼저 점검하는 구간입니다."]
    if positives:
        lines.append("- 반등을 믿으려면 대형주뿐 아니라 소형주, 하이일드 채권, 반도체가 같이 살아나야 합니다.")
    lines.append("- VIX가 25를 넘거나 금리/달러가 같이 오르면 현금 비중을 높이는 쪽이 유리합니다.")
    lines.append("- VIX가 내려가고 RSP/IWM/HYG가 개선되면 분할 접근을 검토할 수 있습니다.")
    return lines[:4]


def _decision_brief(dashboard: Dict[str, Any], engine: Dict[str, Any]) -> List[str]:
    verdict = dashboard.get("verdict", "중립~우호")
    score = dashboard.get("score", 0)
    confidence = engine.get("confidence_score", 0)
    trade_mode = engine.get("trade_mode", "관망")
    position_size = engine.get("position_size", 0)
    if trade_mode == "현금 우선" or verdict == "위험 우위":
        action = "오늘은 신규 매수보다 현금 비중 유지가 우선입니다."
    elif trade_mode == "실전 후보":
        action = "조건이 맞으면 분할매수 후보로 볼 수 있습니다."
    elif trade_mode == "조건부":
        action = "확인 후 소액 분할만 검토하는 쪽이 낫습니다."
    else:
        action = "지금은 관망이 더 안전합니다."
    return [
        f"- 오늘 결론: {verdict} (Risk {score}, 신뢰도 {confidence}, 실행 모드 {trade_mode}, 권장 비중 {position_size}%)",
        f"- 한줄 판단: {action}",
        f"- 진입 조건: {engine.get('entry_condition', 'n/a')}",
        f"- 무효화 조건: {engine.get('invalidation', 'n/a')}",
        f"- 리스크 한도: 일일 손실 -{engine.get('daily_loss_limit_pct', 0.0):.1f}% / 최대 비중 {position_size}% / 레버리지 금지",
    ]


def _market_core_snapshot(data: Dict[str, Any]) -> Dict[str, Any]:
    us = _row_map(data.get("markets", {}).get("US", []))
    korea = _row_map(data.get("markets", {}).get("Korea", []))
    trend = _trend_row_map(data.get("trend", {}))
    one = trend.get("1mo", {})
    three = trend.get("3mo", {})
    macro = data.get("macro", {})
    valuation = data.get("valuation", {})
    return {
        "spy_1mo_pct": one.get("S&P 500 ETF", {}).get("change_pct"),
        "rsp_1mo_pct": one.get("Equal Weight S&P 500", {}).get("change_pct"),
        "iwm_1mo_pct": one.get("Small Caps ETF", {}).get("change_pct"),
        "hyg_1mo_pct": one.get("High Yield Bond ETF", {}).get("change_pct"),
        "semis_1mo_pct": one.get("Semiconductors ETF", {}).get("change_pct"),
        "spy_3mo_pct": three.get("S&P 500 ETF", {}).get("change_pct"),
        "rsp_3mo_pct": three.get("Equal Weight S&P 500", {}).get("change_pct"),
        "vix": us.get("VIX", {}).get("price"),
        "us10y": us.get("US 10Y Yield", {}).get("price"),
        "dxy": us.get("Dollar Index", {}).get("price"),
        "kospi": korea.get("KOSPI", {}).get("price"),
        "kosdaq": korea.get("KOSDAQ", {}).get("price"),
        "usdkrw": korea.get("USD/KRW", {}).get("price"),
        "shiller_pe": _as_float(valuation.get("shiller_pe", {}).get("value")),
        "sp500_pe": _as_float(valuation.get("sp500_pe", {}).get("value")),
        "earnings_yield": _as_float(valuation.get("earnings_yield", {}).get("value")),
        "t10y2y": _as_float(macro.get("T10Y2Y", {}).get("value")),
        "hy_spread": _as_float(macro.get("BAMLH0A0HYM2", {}).get("value")),
    }


def _market_core_data_lines(snapshot: Dict[str, Any]) -> List[str]:
    return [
        f"- 시장 폭: SPY 1개월 {_fmt_pct(snapshot.get('spy_1mo_pct'))}, RSP 1개월 {_fmt_pct(snapshot.get('rsp_1mo_pct'))}, IWM 1개월 {_fmt_pct(snapshot.get('iwm_1mo_pct'))}, HYG 1개월 {_fmt_pct(snapshot.get('hyg_1mo_pct'))}",
        f"- 변동성/달러: VIX {_fmt_num(snapshot.get('vix'), 1)}, US 10Y {_fmt_num(snapshot.get('us10y'), 2)}%, 달러지수 {_fmt_num(snapshot.get('dxy'), 1)}",
        f"- 한국/환율: KOSPI {_fmt_num(snapshot.get('kospi'), 2)}, KOSDAQ {_fmt_num(snapshot.get('kosdaq'), 2)}, USD/KRW {_fmt_num(snapshot.get('usdkrw'), 2)}",
        f"- 밸류/스프레드: CAPE {_fmt_num(snapshot.get('shiller_pe'), 1)}, S&P PE {_fmt_num(snapshot.get('sp500_pe'), 1)}, Earnings Yield {_fmt_num(snapshot.get('earnings_yield'), 2)}%, HY spread {_fmt_num(snapshot.get('hy_spread'), 2)}%",
    ]


def build_decision_snapshot(
    data: Dict[str, Any],
    ai_signal: Optional[Dict[str, Any]] = None,
    dashboard: Optional[Dict[str, Any]] = None,
    engine: Optional[Dict[str, Any]] = None,
    calibration: Optional[Dict[str, Any]] = None,
    reliability: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    dashboard = dashboard or _risk_dashboard(data)
    calibration = calibration or _calibration_profile()
    reliability = reliability or _reliability_profile(calibration)
    engine = engine or _trade_engine(data, dashboard, ai_signal=ai_signal)
    hybrid = _combine_verdicts(dashboard, ai_signal) if ai_signal else None
    core_data = _market_core_snapshot(data)
    core_evidence = _select_core_evidence(dashboard)[:4] or dashboard.get("negatives", [])[:4]
    buffers = dashboard.get("positives", [])[:2]
    if not buffers and dashboard.get("negatives"):
        buffers = dashboard.get("negatives", [])[:1]
    ai_payload = None
    if ai_signal:
        ai_payload = {
            "verdict": ai_signal.get("verdict"),
            "confidence": ai_signal.get("confidence"),
            "summary": ai_signal.get("summary"),
            "action": ai_signal.get("action"),
            "trade_mode": ai_signal.get("trade_mode"),
            "position_size": ai_signal.get("position_size"),
            "entry_condition": ai_signal.get("entry_condition"),
            "invalidation": ai_signal.get("invalidation"),
            "combined_score": hybrid["score"] if hybrid else None,
            "rule_verdict": hybrid["rule_verdict"] if hybrid else None,
            "rule_score": hybrid["rule_score"] if hybrid else None,
            "ai_verdict": hybrid["ai_verdict"] if hybrid else None,
            "ai_confidence": hybrid["ai_confidence"] if hybrid else None,
        }
    generated_at = data.get("generated_at") or dt.datetime.now(REPORT_TIMEZONE).isoformat(timespec="seconds")
    return {
        "project_id": "market-agent",
        "snapshot_version": 1,
        "record_type": "decision",
        "generated_at": generated_at,
        "report_kind": "daily",
        "report_date": dt.datetime.now(REPORT_TIMEZONE).strftime("%Y-%m-%d"),
        "dashboard": {
            "verdict": dashboard.get("verdict"),
            "score": dashboard.get("score"),
            "positives": dashboard.get("positives", []),
            "negatives": dashboard.get("negatives", []),
        },
        "engine": {
            "quality_score": engine.get("quality_score"),
            "confidence_score": engine.get("confidence_score"),
            "trade_mode": engine.get("trade_mode"),
            "position_size": engine.get("position_size"),
            "daily_loss_limit_pct": engine.get("daily_loss_limit_pct"),
            "leverage_allowed": engine.get("leverage_allowed"),
            "calibration_state": engine.get("calibration_state"),
            "calibration_sample_count": engine.get("calibration_sample_count"),
            "calibration_position_cap": engine.get("calibration_position_cap"),
            "entry_condition": engine.get("entry_condition"),
            "invalidation": engine.get("invalidation"),
        },
        "calibration": {
            "sample_count": calibration.get("sample_count", 0),
            "walk_forward_mae": calibration.get("walk_forward_mae"),
            "walk_forward_direction_hit_rate": calibration.get("walk_forward_direction_hit_rate"),
            "expected_return_pct": calibration.get("expected_return_pct"),
            "recommended_position_multiplier": calibration.get("recommended_position_multiplier"),
            "avg_abs_return_pct": calibration.get("avg_abs_return_pct"),
            "benchmark_weights": calibration.get("benchmark_weights", {}),
            "benchmark_return_pct": calibration.get("benchmark_return_pct"),
            "model": calibration.get("model", {}),
        },
        "reliability": reliability,
        "core_data": core_data,
        "core_evidence": core_evidence,
        "buffers": buffers,
        "ai_signal": ai_payload,
        "trade_mode": engine.get("trade_mode"),
        "position_size": engine.get("position_size"),
        "decision_brief": _decision_brief(dashboard, engine),
    }


def _brief_report_lines(data: Dict[str, Any], dashboard: Dict[str, Any], engine: Dict[str, Any], ai_signal: Optional[Dict[str, Any]] = None) -> str:
    today = dt.datetime.now(REPORT_TIMEZONE).strftime("%Y-%m-%d")
    hybrid = _combine_verdicts(dashboard, ai_signal) if ai_signal else None
    if ai_signal and hybrid:
        ai_signal = dict(ai_signal)
        ai_signal["combined_score"] = hybrid["score"]
        ai_signal["rule_verdict"] = hybrid["rule_verdict"]
        ai_signal["rule_score"] = hybrid["rule_score"]
        ai_signal["ai_verdict"] = hybrid["ai_verdict"]
        ai_signal["ai_confidence"] = hybrid["ai_confidence"]
    core_evidence = _select_core_evidence(dashboard)[:3] or dashboard.get("negatives", [])[:3]
    buffers = dashboard.get("positives", [])[:2]
    if not buffers and dashboard.get("negatives"):
        buffers = dashboard.get("negatives", [])[:1]
    regime = _regime_label(data, dashboard)
    freshness = engine.get("freshness", {})
    quality_score = engine.get("quality_score", 0)
    confidence_score = engine.get("confidence_score", 0)
    core_data = _market_core_snapshot(data)
    lines = [
        f"[{today} 글로벌 시장 브리핑]",
        "",
        (
            f"판단: {hybrid['verdict']} | 기계 {dashboard['verdict']}({dashboard['score']}) | "
            f"데이터 품질: {quality_score}/100 | 신뢰도: {confidence_score}/100"
            if hybrid
            else f"판단: {dashboard['verdict']} | 데이터 품질: {quality_score}/100 | 신뢰도: {confidence_score}/100 | Risk {dashboard['score']}"
        ),
        f"시장 국면: {regime} | 데이터 신선도: {freshness.get('score', 0)}/100",
        f"백테스트 상태: {engine.get('calibration_state', '미검증')} | 표본 {engine.get('calibration_sample_count', 0)} | 비중 상한 x{engine.get('calibration_position_cap', 1.0):.2f}",
        _plain_verdict(dashboard),
        "",
        "[핵심 데이터]",
        *_market_core_data_lines(core_data),
        "",
        "[핵심 결론]",
        *_decision_brief(dashboard, engine),
        "",
        "[핵심 근거]",
        *(f"- {_plain_reason(item)}" for item in core_evidence),
    ]
    if buffers:
        lines.extend(["", "[완충 근거]", *(f"- {_plain_reason(item)}" for item in buffers)])
    if ai_signal and hybrid:
        lines.extend(
            [
                "",
                "[AI 보조 판단]",
                f"- AI verdict: {ai_signal.get('verdict', 'n/a')} (confidence {ai_signal.get('confidence', 0)}%)",
            ]
        )
        if ai_signal.get("summary"):
            lines.append(f"- 한줄 요약: {ai_signal['summary']}")
        if ai_signal.get("action"):
            lines.append(f"- AI 한줄 대응: {ai_signal['action']}")
    lines.extend(
        [
            "",
            "[리스크 체크]",
            *_final_checkpoints(data)[:3],
            "",
            "주의: 투자 조언이 아닌 데이터 기반 참고 브리핑입니다.",
        ]
    )
    return "\n".join(lines)


def _ai_section_lines(ai_signal: Dict[str, Any], rule_dashboard: Dict[str, Any]) -> List[str]:
    lines = [
        "- 역할: 룰 점수에 없는 뉴스, 맥락, 예외를 한 번 더 보는 보조 의견입니다.",
        f"- AI verdict: {ai_signal.get('verdict', 'n/a')} (confidence {ai_signal.get('confidence', 0)}%)",
        f"- 룰 대비: {rule_dashboard.get('verdict', 'n/a')}({rule_dashboard.get('score', 0)}) -> combined {ai_signal.get('combined_score', 'n/a')}",
    ]
    summary = ai_signal.get("summary")
    if summary:
        lines.append(f"- 한줄 요약: {summary}")
    key_factors = ai_signal.get("key_factors") or []
    if key_factors:
        lines.append("- AI가 본 핵심:")
        lines.extend(f"  - {item}" for item in key_factors[:4])
    missing_data = ai_signal.get("missing_data") or []
    if missing_data:
        lines.append("- AI가 부족하다고 본 데이터:")
        lines.extend(f"  - {item}" for item in missing_data[:4])
    action = ai_signal.get("action")
    if action:
        lines.append(f"- AI 한줄 대응: {action}")
    trade_mode = ai_signal.get("trade_mode")
    if trade_mode:
        lines.append(f"- AI 실행 모드: {trade_mode}")
    position_size = ai_signal.get("position_size")
    if position_size is not None:
        lines.append(f"- AI 권장 비중: {position_size}%")
    entry_condition = ai_signal.get("entry_condition")
    if entry_condition:
        lines.append(f"- AI 진입 조건: {entry_condition}")
    invalidation = ai_signal.get("invalidation")
    if invalidation:
        lines.append(f"- AI 무효화 조건: {invalidation}")
    return lines


def _openai_failed_softly(exc: Exception) -> bool:
    text = str(exc).lower()
    return "429" in text or "rate limit" in text or "rate_limit_exceeded" in text


def _trade_engine_lines(data: Dict[str, Any], dashboard: Dict[str, Any], ai_signal: Optional[Dict[str, Any]] = None) -> List[str]:
    engine = _trade_engine(data, dashboard, ai_signal=ai_signal)
    critical_missing = engine.get("critical_missing", [])
    missing = engine.get("quality_missing", [])
    critical_labels = {
        "us_quotes": "미국 시세",
        "korea_quotes": "한국 시세",
        "china_quotes": "중국 시세",
        "internals": "시장 내부",
        "trend": "추세",
        "macro": "거시 금리",
        "valuation": "밸류에이션",
        "news": "뉴스",
        "fear_greed": "심리",
    }
    lines = [
        "[실전 매매 엔진]",
        f"- 데이터 품질: {engine['quality_score']}/100",
        f"- 신뢰도 점수: {engine['confidence_score']}/100",
        f"- 백테스트 상태: {engine.get('calibration_state', '미검증')} (샘플 {engine.get('calibration_sample_count', 0)}, 비중 상한 x{engine.get('calibration_position_cap', 1.0):.2f})",
        f"- 실행 모드: {engine['trade_mode']}",
        f"- 권장 비중: {engine['position_size']}% of normal size",
        f"- 진입 조건: {engine['entry_condition']}",
        f"- 무효화 조건: {engine['invalidation']}",
        f"- AI/룰 정합성: {engine['ai_alignment']}",
    ]
    if critical_missing:
        lines.append("- 빠진 핵심 데이터: " + ", ".join(critical_labels.get(item, item) for item in critical_missing))
    elif missing:
        lines.append("- 부족한 참고 데이터: " + ", ".join(missing))
    if engine["confidence_score"] < 60:
        lines.append("- 해석: 아직은 실전 진입보다 관망이 더 안전합니다.")
    elif engine["trade_mode"] == "실전 후보":
        lines.append("- 해석: 조건이 맞으면 실전 진입 후보로 볼 수 있습니다.")
    else:
        lines.append("- 해석: 조건을 더 확인한 뒤 단계적으로 접근하는 편이 낫습니다.")
    if engine.get("calibration_note"):
        lines.append(f"- 자동 개선 메모: {engine['calibration_note']}")
    return lines


def _calibration_profile() -> Dict[str, Any]:
    try:
        from .config import get_settings
        from .evaluation import build_calibration_profile
    except Exception:
        return {
            "sample_count": 0,
            "walk_forward_mae": None,
            "walk_forward_direction_hit_rate": None,
            "model": {"slope": 0.0, "intercept": 0.0, "r2": 0.0},
            "expected_return_pct": 0.0,
            "recommended_position_multiplier": 0.0,
            "avg_abs_return_pct": 0.0,
            "verdict_stats": {},
        }

    settings = get_settings()
    return build_calibration_profile(table_name=settings.history_table_name, window_days=60)


def _reliability_profile(calibration: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    calibration = calibration or _calibration_profile()
    try:
        from .evaluation import build_reliability_guard
    except Exception:
        return {
            "state": "미검증",
            "sample_count": int(calibration.get("sample_count", 0) or 0),
            "confidence_delta": 0,
            "position_multiplier_cap": 1.0,
            "max_trade_mode": "실전 후보",
            "note": "자동 보정 불가",
        }
    return build_reliability_guard(calibration, context="market")


def _price_trade_levels(
    current_price: Optional[float],
    expected_return_pct: float,
    confidence_score: int,
    avg_abs_return_pct: float,
    asset_kind: str,
    trade_mode: str,
) -> Dict[str, Any]:
    if not isinstance(current_price, (int, float)) or current_price <= 0:
        return {
            "entry_price": None,
            "stop_price": None,
            "take_price": None,
            "entry_offset_pct": None,
            "stop_pct": None,
            "take_pct": None,
        }
    if asset_kind == "market":
        base_stop = 1.2
        base_pullback = 0.35
        reward_floor = 1.8
        stop_cap = 4.0
    else:
        base_stop = 3.2
        base_pullback = 0.9
        reward_floor = 2.1
        stop_cap = 8.5
    stop_pct = min(
        stop_cap,
        max(
            base_stop,
            avg_abs_return_pct * (0.55 if asset_kind == "market" else 0.45),
            (0.8 if confidence_score >= 65 else 1.2) + (0.15 * max(confidence_score - 50, 0) / 10.0),
        ),
    )
    if expected_return_pct > 0:
        entry_offset_pct = base_pullback if confidence_score >= 65 else base_pullback + (0.3 if asset_kind == "market" else 0.7)
    else:
        entry_offset_pct = base_pullback + (0.8 if asset_kind == "market" else 1.5)
    if trade_mode in {"관망", "현금 우선"}:
        entry_offset_pct += 0.5 if asset_kind == "market" else 1.0
        stop_pct += 0.3 if asset_kind == "market" else 0.6
    take_pct = max(
        stop_pct * reward_floor,
        abs(expected_return_pct) * (2.0 if asset_kind == "market" else 2.4) + (0.8 if asset_kind == "market" else 1.5),
    )
    entry_price = current_price * (1 - entry_offset_pct / 100.0)
    stop_price = entry_price * (1 - stop_pct / 100.0)
    take_price = entry_price * (1 + take_pct / 100.0)
    return {
        "entry_price": entry_price,
        "stop_price": stop_price,
        "take_price": take_price,
        "entry_offset_pct": entry_offset_pct,
        "stop_pct": stop_pct,
        "take_pct": take_pct,
    }


def _trade_plan_lines(data: Dict[str, Any], dashboard: Dict[str, Any], engine: Dict[str, Any]) -> List[str]:
    calibration = _calibration_profile()
    expected_return_pct = float(calibration.get("expected_return_pct", 0.0) or 0.0)
    model = calibration.get("model", {})
    quality_score = int(engine.get("quality_score", 0) or 0)
    confidence_score = int(engine.get("confidence_score", 0) or 0)
    final_size = int(
        max(
            0,
            min(
                100,
                round(
                    (engine.get("position_size", 0) or 0)
                    * float(calibration.get("recommended_position_multiplier", 0.0) or 0.0)
                ),
            ),
        )
    )
    if confidence_score < 55 or calibration.get("sample_count", 0) < 3:
        final_size = min(final_size, 25)
    if expected_return_pct <= 0 and dashboard.get("verdict") != "중립~우호":
        final_size = 0
    us_rows = _row_map(data.get("markets", {}).get("US", []))
    korea_rows = _row_map(data.get("markets", {}).get("Korea", []))
    plan_assets = [
        ("SPY", us_rows.get("S&P 500", {}).get("price"), "미국 시장"),
        ("KOSPI", korea_rows.get("KOSPI", {}).get("price"), "한국 시장"),
    ]
    lines = [
        "[walk-forward 보정]",
        f"- 샘플 개수: {calibration['sample_count']}",
        f"- 회귀모형: 기대수익 = {model.get('slope', 0.0):+.4f} * score + {model.get('intercept', 0.0):+.4f}",
        f"- 방향 적중률: {((calibration.get('walk_forward_direction_hit_rate') or 0.0) * 100):.1f}%",
        f"- walk-forward MAE: {calibration['walk_forward_mae']:.2f}%" if calibration.get("walk_forward_mae") is not None else "- walk-forward MAE: n/a",
        f"- 현재 점수 기준 기대수익: {expected_return_pct:+.2f}%",
        (
            f"- KRW 바스켓 비중: S&P 500 {float(calibration.get('benchmark_weights', {}).get('left_weight', 0.5)) * 100:.0f}% / "
            f"KOSPI {float(calibration.get('benchmark_weights', {}).get('right_weight', 0.5)) * 100:.0f}%"
        ),
        "",
        "[매매 포맷]",
    ]
    for symbol, price, label in plan_assets:
        trade = _price_trade_levels(
            price,
            expected_return_pct=expected_return_pct,
            confidence_score=confidence_score,
            avg_abs_return_pct=float(calibration.get("avg_abs_return_pct", 0.0) or 0.0),
            asset_kind="market",
            trade_mode=str(engine.get("trade_mode", "")),
        )
        if price is None or trade["entry_price"] is None:
            lines.append(f"- {label}: 가격 수집 실패")
            continue
        lines.append(
            f"- {label} ({symbol}): 현재가 {_fmt_num(price, 2)}, "
            f"진입가 {_fmt_num(trade['entry_price'], 2)}, 손절가 {_fmt_num(trade['stop_price'], 2)}, "
            f"익절가 {_fmt_num(trade['take_price'], 2)}, 비중 {final_size}%"
        )
    lines.append(f"- 비중 해석: 기본 엔진 {engine.get('position_size', 0)}%에 보정 멀티플 {float(calibration.get('recommended_position_multiplier', 0.0) or 0.0):.2f}를 곱한 값입니다.")
    if final_size == 0:
        lines.append("- 실행 해석: 아직은 실전 진입보다 관망/축소가 우선입니다.")
    else:
        lines.append("- 실행 해석: 조건부 분할 진입이 가능하지만, 손절가를 먼저 지키는 전제로만 봅니다.")
    return lines


def _sector_recommendations(data: Dict[str, Any]) -> List[str]:
    dashboard = _risk_dashboard(data)
    sector_rows = _row_map(data.get("sectors", {}).get("items", []))
    internals = _row_map(data.get("internals", {}).get("items", []))
    us = _row_map(data.get("markets", {}).get("US", []))

    risk_score = dashboard.get("score", 0)
    vix = us.get("VIX", {}).get("price")
    tnx = us.get("US 10Y Yield", {}).get("price")
    spy_change = internals.get("S&P 500 ETF", {}).get("change_pct")

    display_names = {
        "Technology": "기술주(XLK)",
        "Semiconductors": "반도체(SMH)",
        "Software": "소프트웨어(IGV)",
        "Communication Services": "커뮤니케이션(XLC)",
        "Consumer Discretionary": "경기소비재(XLY)",
        "Financials": "금융(XLF)",
        "Regional Banks": "지역은행(KRE)",
        "Industrials": "산업재(XLI)",
        "Energy": "에너지(XLE)",
        "Materials": "소재(XLB)",
        "Health Care": "헬스케어(XLV)",
        "Consumer Staples": "필수소비재(XLP)",
        "Utilities": "유틸리티(XLU)",
        "Real Estate": "리츠/부동산(XLRE)",
        "Gold Miners": "금광주(GDX)",
    }

    defensive = {"Health Care", "Consumer Staples", "Utilities"}
    quality_growth = {"Technology", "Software", "Communication Services"}
    cyclical = {"Consumer Discretionary", "Financials", "Industrials", "Materials", "Regional Banks"}
    inflation_hedge = {"Energy", "Gold Miners"}
    high_beta_growth = {"Semiconductors", "Consumer Discretionary", "Regional Banks"}

    scored = []
    for label, row in sector_rows.items():
        change = row.get("change_pct")
        if not isinstance(change, (int, float)):
            continue
        score = change
        reasons = [f"{row.get('symbol')} {_fmt_pct(change)}"]
        if risk_score >= 5:
            if label in defensive:
                score += 1.4
                reasons.append("방어적 성격")
            if label in high_beta_growth:
                score -= 1.2
                reasons.append("변동성 큰 구간에서는 비중 확대 보류")
        if isinstance(vix, (int, float)) and vix >= 20 and label in defensive:
            score += 0.8
            reasons.append("VIX 높을 때 상대적으로 안정적")
        if label in inflation_hedge:
            score += 0.6
            reasons.append("유가/물가 뉴스 헤지")
        if label == "Real Estate" and isinstance(tnx, (int, float)) and tnx >= 4.5:
            score -= 1.8
            reasons.append("고금리 부담")
        if isinstance(spy_change, (int, float)) and change > spy_change + 0.8:
            score += 0.7
            reasons.append("시장 대비 상대강도 양호")
        if label in quality_growth and risk_score <= 4:
            score += 0.6
            reasons.append("위험 완화 시 성장주 우선 후보")
        if label in cyclical and risk_score >= 5 and change < 0:
            score -= 0.5
            reasons.append("경기민감주는 확인 필요")
        scored.append((score, label, reasons))

    if not scored:
        return ["- 섹터 데이터 수집 실패로 추천 후보를 만들지 못했습니다."]

    scored.sort(reverse=True)
    preferred = scored[:3]
    avoid = sorted(scored, key=lambda item: item[0])[:2]

    lines = ["- 우선 관심: " + ", ".join(display_names.get(label, label) for _, label, _ in preferred)]
    for _, label, reasons in preferred:
        lines.append(f"  - {display_names.get(label, label)}: {'; '.join(reasons[:3])}")
    if risk_score >= 5:
        lines.append("- 지금 방식: 한 번에 크게 사기보다 방어 섹터와 현금 비중을 섞고, 성장주는 분할 접근이 낫습니다.")
    else:
        lines.append("- 지금 방식: 주도 섹터 중심으로 분할 접근하되, VIX/금리 재상승 시 속도를 줄입니다.")
    lines.append("- 보류/주의: " + ", ".join(display_names.get(label, label) for _, label, _ in avoid))
    return lines


def _plain_verdict(dashboard: Dict[str, Any]) -> str:
    verdict = dashboard.get("verdict")
    if verdict == "위험 우위":
        return "쉽게 말해, 지금은 올라갈 가능성보다 흔들릴 가능성을 더 크게 봐야 하는 장입니다."
    if verdict == "주의":
        return "쉽게 말해, 반등은 가능하지만 아직 안심하고 따라붙기엔 확인할 게 남아 있습니다."
    return "쉽게 말해, 큰 위험 신호는 제한적이지만 뉴스와 금리 변화를 계속 확인해야 합니다."


def _risk_notes(data: Dict[str, Any]) -> List[str]:
    notes = []
    us = {row.get("label"): row for row in data.get("markets", {}).get("US", [])}
    macro = data.get("macro", {})
    fear_greed = data.get("fear_greed", {})
    fg_data = fear_greed.get("data", {}) if fear_greed.get("ok") else {}
    vix = us.get("VIX", {}).get("price")
    tnx = us.get("US 10Y Yield", {}).get("price")
    dollar = us.get("Dollar Index", {}).get("change_pct")
    spread_10y2y = macro.get("T10Y2Y", {}).get("value")
    hy_spread = macro.get("BAMLH0A0HYM2", {}).get("value")
    us_fg = (fg_data.get("us") or {}).get("score")
    kr_fg = (fg_data.get("kr") or {}).get("score")
    if isinstance(us_fg, int) and us_fg <= 25:
        notes.append("미국 Fear & Greed가 극단적 공포권이라 과매도 반등과 추가 하락 리스크를 함께 봐야 합니다.")
    if isinstance(kr_fg, int) and kr_fg >= 75:
        notes.append("한국 Fear & Greed가 탐욕권이라 추격 매수보다 수급 지속성을 확인하는 편이 좋습니다.")
    if isinstance(vix, (int, float)):
        if vix >= 25:
            notes.append("VIX가 높은 구간이라 단기 변동성 리스크를 우선 확인하세요.")
        elif vix <= 14:
            notes.append("VIX가 낮은 구간이라 시장이 안도하고 있지만 과도한 낙관도 점검하세요.")
    if isinstance(tnx, (int, float)) and tnx >= 4.5:
        notes.append("미국 10년물 금리 프록시가 높은 편이라 성장주 밸류에이션 부담을 확인하세요.")
    if isinstance(dollar, (int, float)) and dollar > 0.5:
        notes.append("달러 강세가 나타나면 한국/중국 등 비미국 위험자산에는 부담이 될 수 있습니다.")
    if isinstance(spread_10y2y, (int, float)) and spread_10y2y < 0:
        notes.append("미국 10Y-2Y 금리 스프레드가 역전 상태라 경기 둔화 신호를 계속 추적해야 합니다.")
    if isinstance(hy_spread, (int, float)) and hy_spread >= 5:
        notes.append("하이일드 스프레드가 높은 구간이면 신용 리스크 확대 여부를 우선 확인하세요.")
    if not notes:
        notes.append("특정 단일 위험 신호보다 지수, 금리, 달러, 변동성을 함께 확인하세요.")
    return notes


def build_local_report(data: Dict[str, Any], ai_signal: Optional[Dict[str, Any]] = None, concise: bool = False) -> str:
    today = dt.datetime.now(REPORT_TIMEZONE).strftime("%Y-%m-%d")
    dashboard = _risk_dashboard(data)
    confidence = _confidence_label(data, dashboard)
    engine = _trade_engine(data, dashboard, ai_signal=ai_signal)
    hybrid = _combine_verdicts(dashboard, ai_signal) if ai_signal else None
    if ai_signal and hybrid:
        ai_signal = dict(ai_signal)
        ai_signal["combined_score"] = hybrid["score"]
        ai_signal["rule_verdict"] = hybrid["rule_verdict"]
        ai_signal["rule_score"] = hybrid["rule_score"]
        ai_signal["ai_verdict"] = hybrid["ai_verdict"]
        ai_signal["ai_confidence"] = hybrid["ai_confidence"]
    if concise:
        return _brief_report_lines(data, dashboard, engine, ai_signal=ai_signal)
    negatives = _select_core_evidence(dashboard)
    positives = dashboard.get("positives", [])[:2]
    lines = [
        f"[{today} 글로벌 시장 브리핑]",
        "",
        (
            f"판단: {hybrid['verdict']} | 기계 {dashboard['verdict']}({dashboard['score']}) | "
            f"신뢰도: {confidence} | Risk {dashboard['score']}"
            if hybrid
            else f"판단: {dashboard['verdict']} | 신뢰도: {confidence} | Risk {dashboard['score']}"
        ),
        _plain_verdict(dashboard),
        "",
        *_trade_engine_lines(data, dashboard, ai_signal=ai_signal if ai_signal else None),
        "",
        *_trade_plan_lines(data, dashboard, engine),
        "",
        "[핵심 투자 판단 근거]",
        *(f"- {_plain_reason(item)}" for item in negatives),
        *(f"- 완충 요인: {_plain_reason(item)}" for item in positives),
        *(["", "[AI 보조 판단]", *_ai_section_lines(ai_signal, hybrid)] if ai_signal and hybrid else []),
        "",
        "[글로벌 시장 대시보드]",
        *_market_snapshot_lines(data),
        "",
        "[추세 점검]",
        *_trend_lines(data),
        "",
        "[밸류에이션·심리 점검]",
        _valuation_snapshot_line(data),
        "",
        "[오늘의 시장 변수]",
        *_compact_news_lines(data.get("news", {})),
        "",
        "[대응 전략]",
        *_final_checkpoints(data),
        "",
        "[관심 섹터·투자 후보]",
        *_sector_recommendations(data),
        "",
        "주의: 투자 조언이 아닌 데이터 기반 참고 브리핑입니다.",
    ]
    return "\n".join(lines)


def build_ai_report(data: Dict[str, Any], api_key: str, model: str) -> str:
    try:
        prompt = (
            "너는 개인 투자 참고용 글로벌 시장 브리핑 작성자다. "
            "제공된 JSON 데이터만 근거로 한국어 리포트를 작성해라. "
            "없는 숫자를 지어내지 말고, 데이터 수집 실패는 명시해라. "
            "투자 조언/매수매도 지시가 아니라 참고용 요약으로 써라. "
            "텔레그램에서 읽기 좋게 900~1400자 안팎으로 압축해라. "
            "전문 지표는 쓰되 초보자도 이해하게 쉬운 말로 풀어써라. "
            "결론을 먼저 쓰고, 핵심 근거는 최대 4개만 남겨라. "
            "시장 내부, 크레딧, 안전자산, 밸류에이션, 최신 뉴스 헤드라인을 서로 연결해서 분석하되 원자료 설명과 URL은 길게 나열하지 마라. "
            "상승 조건과 하방 위험 조건을 분리하고, 확정적 예측 대신 조건부 판단으로 써라. "
            "마지막에는 사용자가 지금 어떻게 대응하면 좋을지 분할매수/관망/현금비중/리스크관리 관점으로 쉽게 정리해라. "
            "실전 매매 엔진처럼 데이터 품질, 신뢰도, 실행 모드, 진입 조건, 무효화 조건을 분명히 밝혀라. "
            "추천 관심 섹터는 매수 지시가 아니라 우선 관찰/분할 검토 후보로 표현해라. "
            "형식: 판단, 왜 그렇게 보나, 시장 상태, 비싸냐 싸냐, 오늘 신경 쓸 뉴스, 내 대응 가이드, 추천 관심 섹터, 주의문."
        )
        payload = {
            "model": model,
            "input": [
                {"role": "system", "content": prompt},
                {"role": "user", "content": json.dumps(data, ensure_ascii=False)[:50000]},
            ],
        }
        response = post_json("https://api.openai.com/v1/responses", payload, bearer=api_key, timeout=60)
        chunks = []
        for item in response.get("output", []):
            for content in item.get("content", []):
                if content.get("type") == "output_text":
                    chunks.append(content.get("text", ""))
        text = "\n".join(chunk for chunk in chunks if chunk).strip()
        if not text:
            return build_local_report(data)
        return text
    except Exception:
        return build_local_report(data)


def build_report(
    data: Dict[str, Any],
    api_key: str = "",
    model: str = "gpt-4.1-mini",
    concise: bool = False,
    return_snapshot: bool = False,
):
    dashboard = _risk_dashboard(data)
    ai_signal = build_ai_signal(data, api_key=api_key, model=model, dashboard=dashboard) if api_key else None
    report = build_local_report(data, ai_signal=ai_signal, concise=concise)
    if return_snapshot:
        snapshot = build_decision_snapshot(data, ai_signal=ai_signal, dashboard=dashboard)
        return report, snapshot
    return report
