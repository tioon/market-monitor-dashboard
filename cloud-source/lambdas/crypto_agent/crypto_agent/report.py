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


def _fmt_money(value: Any) -> str:
    if value is None:
        return "n/a"
    try:
        value = float(value)
        if abs(value) >= 1_000_000_000_000:
            return f"${value / 1_000_000_000_000:.2f}T"
        if abs(value) >= 1_000_000_000:
            return f"${value / 1_000_000_000:.2f}B"
        if abs(value) >= 1_000_000:
            return f"${value / 1_000_000:.2f}M"
        return f"${value:,.2f}"
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
    return {row.get("id") or row.get("label"): row for row in rows}


def _onchain_metric_value(data: Dict[str, Any], asset: str, metric: str) -> Optional[float]:
    return _as_float(data.get("onchain", {}).get("items", {}).get(asset, {}).get("metrics", {}).get(metric, {}).get("value"))


def _onchain_metric_change_pct_7d(data: Dict[str, Any], asset: str, metric: str) -> Optional[float]:
    return _as_float(data.get("onchain", {}).get("items", {}).get(asset, {}).get("metrics", {}).get(metric, {}).get("change_pct_7d"))


def _fear_greed_latest(data: Dict[str, Any]) -> Dict[str, Any]:
    items = data.get("fear_greed", {}).get("items", [])
    return items[0] if items else {}


def _derivative(data: Dict[str, Any], symbol: str) -> Dict[str, Any]:
    return data.get("derivatives", {}).get("items", {}).get(symbol, {})


def _flow_state(data: Dict[str, Any]) -> Dict[str, Any]:
    stablecoins = data.get("stablecoins", {})
    global_data = data.get("global", {})
    return {
        "stable_7d": stablecoins.get("change_pct_7d"),
        "stable_30d": stablecoins.get("change_pct_30d"),
        "global_24h": global_data.get("market_cap_change_pct_24h_usd"),
        "btc_dom": global_data.get("btc_dominance_pct"),
    }


def _trend_row_map(data: Dict[str, Any]) -> Dict[str, Dict[str, Dict[str, Any]]]:
    items = data.get("trend", {}).get("items", {})
    return {period: _row_map(rows) for period, rows in items.items()}


def _technical_row_map(data: Dict[str, Any]) -> Dict[str, Dict[str, Any]]:
    items = data.get("technical", {}).get("items", [])
    return _row_map(items)


def _trend_lines(data: Dict[str, Any]) -> List[str]:
    trend = _trend_row_map(data)

    def row(period: str, label: str) -> Optional[float]:
        return trend.get(period, {}).get(label, {}).get("change_pct")

    return [
        (
            f"- 1개월 추세: BTC {_fmt_pct(row('1mo', 'Bitcoin'))}, ETH {_fmt_pct(row('1mo', 'Ethereum'))}, "
            f"SOL {_fmt_pct(row('1mo', 'Solana'))}, SPY {_fmt_pct(row('1mo', 'S&P 500 ETF'))}, "
            f"HYG {_fmt_pct(row('1mo', 'High Yield Bond ETF'))}"
        ),
        (
            f"- 3개월 추세: BTC {_fmt_pct(row('3mo', 'Bitcoin'))}, ETH {_fmt_pct(row('3mo', 'Ethereum'))}, "
            f"SOL {_fmt_pct(row('3mo', 'Solana'))}, IWM {_fmt_pct(row('3mo', 'Small Caps ETF'))}, "
            f"LQD {_fmt_pct(row('3mo', 'Investment Grade Bond ETF'))}"
        ),
    ]


def _technical_lines(data: Dict[str, Any]) -> List[str]:
    technical = _technical_row_map(data)

    def row(label: str) -> Dict[str, Any]:
        return technical.get(label, {})

    def fmt_state(row_data: Dict[str, Any]) -> str:
        if not row_data:
            return "데이터 없음"
        if row_data.get("error"):
            return f"수집 실패 ({row_data['error']})"
        return ", ".join(
            [
                f"현재가 {_fmt_money(row_data.get('price'))}",
                f"SMA20 {_fmt_money(row_data.get('sma20'))}",
                f"SMA50 {_fmt_money(row_data.get('sma50'))}",
                f"SMA200 {_fmt_money(row_data.get('sma200'))}",
                f"RSI14 {_fmt_num(row_data.get('rsi14'), 1)}",
            ]
        )

    def mood(row_data: Dict[str, Any]) -> str:
        if not row_data:
            return "차트 데이터 없음"
        if row_data.get("error"):
            return "차트 데이터 없음"
        trend_state = row_data.get("trend_state") or "중립"
        if row_data.get("stack_bullish"):
            return f"{trend_state}, 추세 정배열"
        if row_data.get("above_sma200") is False and isinstance(row_data.get("rsi14"), (int, float)) and row_data.get("rsi14") < 40:
            return "약세 추세, 장기선 하회"
        if row_data.get("above_sma50"):
            return f"{trend_state}, 중기선 상회"
        return trend_state

    return [
        f"- BTC 차트: {fmt_state(row('Bitcoin'))} ({mood(row('Bitcoin'))})",
        f"- ETH 차트: {fmt_state(row('Ethereum'))} ({mood(row('Ethereum'))})",
        f"- SOL 차트: {fmt_state(row('Solana'))} ({mood(row('Solana'))})",
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
        "너는 코인 시장 분석 보조 엔진이다. "
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
    rows = _row_map(data.get("markets", {}).get("items", []))
    global_data = data.get("global", {})
    market_context = _market_context_bundle(data)
    trend = _trend_row_map(data)
    us = market_context["us"]
    macro = market_context["macro"]
    fg = _fear_greed_latest(data)

    score = 0
    positives: List[str] = []
    negatives: List[str] = []

    btc = rows.get("bitcoin", {})
    eth = rows.get("ethereum", {})
    sol = rows.get("solana", {})
    btc_24h = btc.get("change_pct_24h")
    btc_7d = btc.get("change_pct_7d")
    eth_24h = eth.get("change_pct_24h")
    sol_24h = sol.get("change_pct_24h")
    global_24h = global_data.get("market_cap_change_pct_24h_usd")
    btc_dom = global_data.get("btc_dominance_pct")
    fg_value = _as_float(fg.get("value"))
    flow = _flow_state(data)
    stable_7d = flow.get("stable_7d")
    stable_30d = flow.get("stable_30d")
    btc_deriv = _derivative(data, "BTCUSDT")
    eth_deriv = _derivative(data, "ETHUSDT")
    btc_funding = btc_deriv.get("funding_rate_pct_8h")
    btc_oi_3d = btc_deriv.get("open_interest_value_change_pct_3d")
    btc_ls = btc_deriv.get("long_short_ratio")
    eth_funding = eth_deriv.get("funding_rate_pct_8h")
    us_spy = us.get("S&P 500", {}).get("change_pct")
    us_rsp = us.get("Equal Weight S&P 500", {}).get("change_pct")
    us_iwm = us.get("Small Caps ETF", {}).get("change_pct")
    us_vix = us.get("VIX", {}).get("price")
    us_tnx = us.get("US 10Y Yield", {}).get("price")
    us_dollar = us.get("Dollar Index", {}).get("change_pct")
    us_hyg = us.get("High Yield Bond ETF", {}).get("change_pct")
    us_lqd = us.get("Investment Grade Bond ETF", {}).get("change_pct")
    us_spread = _as_float(macro.get("T10Y2Y", {}).get("value"))
    btc_active_7d = _onchain_metric_change_pct_7d(data, "btc", "AdrActCnt")
    btc_tx_7d = _onchain_metric_change_pct_7d(data, "btc", "TxCnt")
    btc_fee_7d = _onchain_metric_change_pct_7d(data, "btc", "FeeTotNtv")
    btc_hash_7d = _onchain_metric_change_pct_7d(data, "btc", "HashRate")
    eth_active_7d = _onchain_metric_change_pct_7d(data, "eth", "AdrActCnt")
    eth_tx_7d = _onchain_metric_change_pct_7d(data, "eth", "TxCnt")
    eth_fee_7d = _onchain_metric_change_pct_7d(data, "eth", "FeeTotNtv")
    technical = _technical_row_map(data)
    btc_tech = technical.get("Bitcoin", {})
    eth_tech = technical.get("Ethereum", {})
    sol_tech = technical.get("Solana", {})
    btc_1m = trend.get("1mo", {}).get("Bitcoin", {}).get("change_pct")
    eth_1m = trend.get("1mo", {}).get("Ethereum", {}).get("change_pct")
    sol_1m = trend.get("1mo", {}).get("Solana", {}).get("change_pct")
    btc_3m = trend.get("3mo", {}).get("Bitcoin", {}).get("change_pct")
    eth_3m = trend.get("3mo", {}).get("Ethereum", {}).get("change_pct")
    sol_3m = trend.get("3mo", {}).get("Solana", {}).get("change_pct")
    spy_1m = trend.get("1mo", {}).get("S&P 500 ETF", {}).get("change_pct")
    hyg_1m = trend.get("1mo", {}).get("High Yield Bond ETF", {}).get("change_pct")
    lqd_1m = trend.get("1mo", {}).get("Investment Grade Bond ETF", {}).get("change_pct")

    if isinstance(btc_24h, (int, float)):
        if btc_24h <= -5:
            score += 2
            negatives.append(f"BTC 24시간 {_fmt_pct(btc_24h)}: 단기 매도 압력이 큼")
        elif btc_24h <= -2:
            score += 1
            negatives.append(f"BTC 24시간 {_fmt_pct(btc_24h)}: 단기 흐름이 약함")
        elif btc_24h >= 4:
            score -= 1
            positives.append(f"BTC 24시간 {_fmt_pct(btc_24h)}: 대표 자산 모멘텀은 우호적")

    if isinstance(btc_7d, (int, float)):
        if btc_7d <= -8:
            score += 2
            negatives.append(f"BTC 7일 {_fmt_pct(btc_7d)}: 하락 추세가 누적됨")
        elif btc_7d >= 8:
            score -= 1
            positives.append(f"BTC 7일 {_fmt_pct(btc_7d)}: 중기 모멘텀이 살아 있음")

    if isinstance(btc_1m, (int, float)):
        if btc_1m <= -8:
            score += 1
            negatives.append(f"BTC 1개월 {_fmt_pct(btc_1m)}: 중기 추세가 약함")
        elif btc_1m >= 8:
            score -= 1
            positives.append(f"BTC 1개월 {_fmt_pct(btc_1m)}: 중기 추세가 우호적")

    if isinstance(btc_3m, (int, float)) and btc_3m <= -15:
        score += 1
        negatives.append(f"BTC 3개월 {_fmt_pct(btc_3m)}: 긴 추세 약세가 남아 있음")

    if isinstance(eth_1m, (int, float)) and isinstance(btc_1m, (int, float)):
        if eth_1m - btc_1m > 3:
            score -= 1
            positives.append(f"ETH 1개월이 BTC보다 {eth_1m - btc_1m:.2f}%p 강해 알트 회복 신호")
        elif eth_1m - btc_1m < -3:
            score += 1
            negatives.append(f"ETH 1개월이 BTC보다 {abs(eth_1m - btc_1m):.2f}%p 약해 알트 회복이 늦음")

    if isinstance(sol_1m, (int, float)) and isinstance(btc_1m, (int, float)) and sol_1m - btc_1m < -5:
        score += 1
        negatives.append("SOL 1개월 추세가 BTC보다 약해 고베타 알트 선호가 약함")

    if isinstance(spy_1m, (int, float)) and spy_1m <= -4:
        score += 1
        negatives.append("미국 주식 1개월 추세가 약해 코인 심리에도 부담")

    if isinstance(hyg_1m, (int, float)) and isinstance(lqd_1m, (int, float)):
        if hyg_1m - lqd_1m > 2:
            score -= 1
            positives.append("1개월 기준 하이일드가 투자등급보다 강해 크레딧 선호가 유지")
        elif hyg_1m - lqd_1m < -2:
            score += 1
            negatives.append("1개월 기준 하이일드가 투자등급보다 약해 위험선호가 둔화")

    if isinstance(eth_24h, (int, float)) and isinstance(btc_24h, (int, float)):
        spread = eth_24h - btc_24h
        if spread < -1.5:
            score += 1
            negatives.append(f"ETH가 BTC보다 {abs(spread):.2f}%p 약해 알트 위험선호가 둔화")
        elif spread > 1.5:
            score -= 1
            positives.append(f"ETH가 BTC보다 {spread:.2f}%p 강해 알트 위험선호가 일부 개선")

    if isinstance(sol_24h, (int, float)) and isinstance(btc_24h, (int, float)) and sol_24h - btc_24h < -3:
        score += 1
        negatives.append("SOL 등 고베타 코인이 BTC보다 약해 투기 심리가 식는 모습")

    if isinstance(global_24h, (int, float)):
        if global_24h <= -4:
            score += 2
            negatives.append(f"전체 코인 시총 24시간 {_fmt_pct(global_24h)}: 시장 전반 매도세")
        elif global_24h >= 3:
            score -= 1
            positives.append(f"전체 코인 시총 24시간 {_fmt_pct(global_24h)}: 유동성 유입은 양호")

    if isinstance(stable_7d, (int, float)):
        if stable_7d <= -1:
            score += 2
            negatives.append(f"스테이블코인 공급 7일 {_fmt_pct(stable_7d)}: 신규 대기자금이 줄어드는 신호")
        elif stable_7d <= -0.3:
            score += 1
            negatives.append(f"스테이블코인 공급 7일 {_fmt_pct(stable_7d)}: 유동성은 약간 빠지는 중")
        elif stable_7d >= 1:
            score -= 2
            positives.append(f"스테이블코인 공급 7일 {_fmt_pct(stable_7d)}: 코인 시장 대기자금 유입 신호")
        elif stable_7d >= 0.3:
            score -= 1
            positives.append(f"스테이블코인 공급 7일 {_fmt_pct(stable_7d)}: 유동성은 소폭 개선")

    if isinstance(stable_30d, (int, float)) and stable_30d <= -3:
        score += 1
        negatives.append(f"스테이블코인 공급 30일 {_fmt_pct(stable_30d)}: 중기 유동성 축소")

    if isinstance(btc_dom, (int, float)) and btc_dom >= 58:
        score += 1
        negatives.append(f"BTC 도미넌스 {btc_dom:.1f}%: 방어적으로 BTC에 쏠리는 장세")

    if isinstance(fg_value, (int, float)):
        label = fg.get("classification") or "n/a"
        if fg_value <= 20:
            score += 1
            positives.append(f"Fear & Greed {fg_value:.0f}({label}): 과도한 공포는 반등 여지를 만들 수 있음")
        elif fg_value >= 75:
            score += 2
            negatives.append(f"Fear & Greed {fg_value:.0f}({label}): 탐욕권이라 추격 매수 리스크가 큼")
        elif fg_value >= 60:
            score += 1
            negatives.append(f"Fear & Greed {fg_value:.0f}({label}): 낙관이 커져 변동성 확대를 경계")

    if isinstance(btc_funding, (int, float)):
        if btc_funding >= 0.05:
            score += 2
            negatives.append(f"BTC 펀딩비 8h {_fmt_pct(btc_funding)}: 롱 레버리지가 과열")
        elif btc_funding >= 0.02:
            score += 1
            negatives.append(f"BTC 펀딩비 8h {_fmt_pct(btc_funding)}: 롱 포지션 비용이 높아짐")
        elif btc_funding <= -0.03:
            score += 1
            positives.append(f"BTC 펀딩비 8h {_fmt_pct(btc_funding)}: 숏 쏠림이면 단기 반등 연료가 될 수 있음")

    if isinstance(eth_funding, (int, float)) and eth_funding >= 0.04:
        score += 1
        negatives.append(f"ETH 펀딩비 8h {_fmt_pct(eth_funding)}: 알트 레버리지 과열 경계")

    if isinstance(btc_oi_3d, (int, float)) and isinstance(btc_7d, (int, float)):
        if btc_oi_3d >= 8 and btc_7d < 0:
            score += 2
            negatives.append(f"BTC 미결제약정 3일 {_fmt_pct(btc_oi_3d)}: 가격 약세 속 레버리지 누적")
        elif btc_oi_3d <= -8 and btc_7d < 0:
            score -= 1
            positives.append(f"BTC 미결제약정 3일 {_fmt_pct(btc_oi_3d)}: 청산 이후 과열은 일부 해소")

    if isinstance(btc_ls, (int, float)):
        if btc_ls >= 2.4:
            score += 1
            negatives.append(f"BTC 롱/숏 계정비 {btc_ls:.2f}: 롱 쏠림이 커서 변동성 리스크")
        elif btc_ls <= 0.8:
            score += 1
            positives.append(f"BTC 롱/숏 계정비 {btc_ls:.2f}: 숏 쏠림이면 반등 압력이 생길 수 있음")

    if isinstance(btc_active_7d, (int, float)) and isinstance(btc_tx_7d, (int, float)):
        if btc_active_7d <= -10 and btc_tx_7d <= -10:
            score += 1
            negatives.append(f"BTC 온체인 활동 7일: 활성주소 {_fmt_pct(btc_active_7d)}, 거래 {_fmt_pct(btc_tx_7d)}로 네트워크 사용 둔화")
        elif btc_active_7d >= 10 and btc_tx_7d >= 10:
            score -= 1
            positives.append(f"BTC 온체인 활동 7일: 활성주소 {_fmt_pct(btc_active_7d)}, 거래 {_fmt_pct(btc_tx_7d)}로 네트워크 사용 확대")

    if isinstance(btc_hash_7d, (int, float)):
        if btc_hash_7d <= -5:
            score += 1
            negatives.append(f"BTC 해시레이트 7일 {_fmt_pct(btc_hash_7d)}: 채굴 보안/활동이 둔화")
        elif btc_hash_7d >= 5:
            score -= 0.5
            positives.append(f"BTC 해시레이트 7일 {_fmt_pct(btc_hash_7d)}: 네트워크 보안과 활동이 견조")

    if isinstance(eth_active_7d, (int, float)) and isinstance(eth_tx_7d, (int, float)):
        if eth_active_7d >= 10 and eth_tx_7d >= 10:
            score -= 1
            positives.append(f"ETH 온체인 활동 7일: 활성주소 {_fmt_pct(eth_active_7d)}, 거래 {_fmt_pct(eth_tx_7d)}로 사용성 확대")
        elif eth_active_7d <= -10 and eth_tx_7d <= -10:
            score += 1
            negatives.append(f"ETH 온체인 활동 7일: 활성주소 {_fmt_pct(eth_active_7d)}, 거래 {_fmt_pct(eth_tx_7d)}로 사용성 둔화")

    if isinstance(btc_fee_7d, (int, float)) and btc_fee_7d >= 20:
        score -= 0.5
        positives.append(f"BTC 수수료 7일 {_fmt_pct(btc_fee_7d)}: 네트워크 수요가 커진 흔적")
    if isinstance(eth_fee_7d, (int, float)) and eth_fee_7d >= 20:
        score -= 0.5
        positives.append(f"ETH 수수료 7일 {_fmt_pct(eth_fee_7d)}: 네트워크 수요가 커진 흔적")

    if not btc_tech.get("error"):
        btc_price = _as_float(btc_tech.get("price"))
        btc_sma20 = _as_float(btc_tech.get("sma20"))
        btc_sma50 = _as_float(btc_tech.get("sma50"))
        btc_sma200 = _as_float(btc_tech.get("sma200"))
        btc_rsi = _as_float(btc_tech.get("rsi14"))
        if (
            isinstance(btc_price, (int, float))
            and isinstance(btc_sma20, (int, float))
            and isinstance(btc_sma50, (int, float))
            and isinstance(btc_sma200, (int, float))
            and btc_price >= btc_sma20 >= btc_sma50 >= btc_sma200
            and isinstance(btc_rsi, (int, float))
            and 45 <= btc_rsi <= 70
        ):
            score -= 1
            positives.append(
                f"BTC 차트 정배열: 현재가가 SMA20/50/200 위이고 RSI {btc_rsi:.1f}로 추세 우호"
            )
        elif (
            isinstance(btc_price, (int, float))
            and isinstance(btc_sma200, (int, float))
            and btc_price < btc_sma200
            and isinstance(btc_rsi, (int, float))
            and btc_rsi < 40
        ):
            score += 1
            negatives.append(
                f"BTC 차트 약세: 현재가가 SMA200 아래이고 RSI {btc_rsi:.1f}로 반등 힘이 약함"
            )

    if not eth_tech.get("error") and not btc_tech.get("error"):
        eth_price = _as_float(eth_tech.get("price"))
        eth_sma50 = _as_float(eth_tech.get("sma50"))
        eth_rsi = _as_float(eth_tech.get("rsi14"))
        btc_dist_50 = _as_float(btc_tech.get("dist_sma50_pct"))
        if (
            isinstance(eth_price, (int, float))
            and isinstance(eth_sma50, (int, float))
            and eth_price >= eth_sma50
            and isinstance(eth_rsi, (int, float))
            and eth_rsi >= 50
            and isinstance(btc_dist_50, (int, float))
        ):
            score -= 0.5
            positives.append("ETH 차트가 중기선 위에서 버티며 알트 회복 확인 신호")
        elif isinstance(eth_rsi, (int, float)) and eth_rsi < 40 and isinstance(btc_dist_50, (int, float)) and btc_dist_50 < 0:
            score += 0.5
            negatives.append("ETH 차트 모멘텀이 약해 BTC 대비 알트 확장 신뢰도가 낮음")

    if not sol_tech.get("error"):
        sol_price = _as_float(sol_tech.get("price"))
        sol_sma20 = _as_float(sol_tech.get("sma20"))
        sol_rsi = _as_float(sol_tech.get("rsi14"))
        if (
            isinstance(sol_price, (int, float))
            and isinstance(sol_sma20, (int, float))
            and sol_price >= sol_sma20
            and isinstance(sol_rsi, (int, float))
            and sol_rsi >= 55
        ):
            score -= 0.25
            positives.append("SOL 차트 모멘텀이 살아 있어 고베타 회복 확인에 도움")
        elif (
            isinstance(sol_price, (int, float))
            and isinstance(sol_sma20, (int, float))
            and sol_price < sol_sma20
            and isinstance(sol_rsi, (int, float))
            and sol_rsi < 45
        ):
            score += 0.25
            negatives.append("SOL 차트가 약해 고베타 알트 심리가 아직 약함")

    if isinstance(us_vix, (int, float)):
        if us_vix >= 25:
            score += 1
            negatives.append(f"미국 VIX {us_vix:.1f}: 주식 변동성이 높아 위험자산 전반에 부담")
        elif us_vix <= 14:
            score -= 1
            positives.append(f"미국 VIX {us_vix:.1f}: 위험자산 전반의 공포 압력은 낮음")

    if isinstance(us_spy, (int, float)) and us_spy >= 1:
        score -= 1
        positives.append(f"미국 S&P 500 {_fmt_pct(us_spy)}: 위험자산 심리는 양호")
    elif isinstance(us_spy, (int, float)) and us_spy <= -1:
        score += 1
        negatives.append(f"미국 S&P 500 {_fmt_pct(us_spy)}: 주식 모멘텀이 약해 코인에도 부담")

    if isinstance(us_rsp, (int, float)) and isinstance(us_spy, (int, float)):
        breadth_gap = us_rsp - us_spy
        if breadth_gap > 0.5:
            score -= 1
            positives.append(f"미국 동일가중이 시총가중보다 {breadth_gap:.2f}%p 강해 시장 폭이 개선")
        elif breadth_gap < -0.5:
            score += 1
            negatives.append(f"미국 동일가중이 시총가중보다 {abs(breadth_gap):.2f}%p 약해 상승 폭이 좁음")

    if isinstance(us_iwm, (int, float)) and isinstance(us_spy, (int, float)) and us_iwm - us_spy < -0.8:
        score += 1
        negatives.append("미국 소형주가 대형주보다 약해 위험선호 확장이 약함")

    if isinstance(us_hyg, (int, float)) and isinstance(us_lqd, (int, float)):
        credit_gap = us_hyg - us_lqd
        if credit_gap > 0.3:
            score -= 1
            positives.append(f"미국 하이일드가 투자등급보다 {credit_gap:.2f}%p 강해 크레딧 선호가 유지")
        elif credit_gap < -0.3:
            score += 1
            negatives.append(f"미국 하이일드가 투자등급보다 {abs(credit_gap):.2f}%p 약해 크레딧 선호가 둔화")

    if isinstance(us_tnx, (int, float)) and us_tnx >= 4.5:
        score += 1
        negatives.append(f"미국 10년물 {us_tnx:.2f}%: 할인율 부담이 커짐")

    if isinstance(us_dollar, (int, float)) and us_dollar > 0.4:
        score += 1
        negatives.append(f"달러지수 {_fmt_pct(us_dollar)}: 비미국 위험자산에 부담")

    if isinstance(us_spread, (int, float)) and us_spread < 0:
        score += 1
        negatives.append(f"10Y-2Y 스프레드 {us_spread:.2f}%: 경기 둔화 우려가 남음")

    if score >= 6:
        verdict = "위험 우위"
        stance = "지금은 신규 매수보다 손실 가능성, 현금 비중, 레버리지 축소를 먼저 봐야 합니다."
    elif score >= 3:
        verdict = "주의"
        stance = "분할 접근은 가능하지만 BTC 회복, 유동성 개선, 레버리지 완화를 확인해야 합니다."
    else:
        verdict = "중립~우호"
        stance = "과열 신호가 크지 않다면 메이저 중심 분할 접근은 가능하지만 레버리지는 낮게 둡니다."

    return {
        "score": score,
        "verdict": verdict,
        "stance": stance,
        "positives": positives[:4],
        "negatives": negatives[:8],
        "conditions": [
            "상승 지속 조건: BTC 7일 흐름이 회복되고, ETH가 BTC보다 강하며, 스테이블코인 공급이 늘어야 합니다.",
            "하방 확대 조건: BTC 약세 속 미결제약정/펀딩비가 오르거나 스테이블코인 공급이 줄면 방어적으로 봅니다.",
        ],
    }


def _market_snapshot_lines(data: Dict[str, Any]) -> List[str]:
    rows = _row_map(data.get("markets", {}).get("items", []))
    global_data = data.get("global", {})
    btc = rows.get("bitcoin", {})
    eth = rows.get("ethereum", {})
    sol = rows.get("solana", {})
    xrp = rows.get("ripple", {})
    return [
        (
            f"- BTC: {_fmt_money(btc.get('price'))} "
            f"(24h {_fmt_pct(btc.get('change_pct_24h'))}, 7d {_fmt_pct(btc.get('change_pct_7d'))})"
        ),
        (
            f"- ETH: {_fmt_money(eth.get('price'))} "
            f"(24h {_fmt_pct(eth.get('change_pct_24h'))}, 7d {_fmt_pct(eth.get('change_pct_7d'))})"
        ),
        (
            f"- 고베타: SOL {_fmt_pct(sol.get('change_pct_24h'))}, XRP {_fmt_pct(xrp.get('change_pct_24h'))}"
        ),
        (
            f"- 전체 시총: {_fmt_money(global_data.get('total_market_cap_usd'))} "
            f"(24h {_fmt_pct(global_data.get('market_cap_change_pct_24h_usd'))})"
        ),
        (
            f"- 도미넌스: BTC {_fmt_num(global_data.get('btc_dominance_pct'), 1)}%, "
            f"ETH {_fmt_num(global_data.get('eth_dominance_pct'), 1)}%"
        ),
    ]


def _market_context_bundle(data: Dict[str, Any]) -> Dict[str, Dict[str, Any]]:
    context = data.get("market_context", {})
    quotes = context.get("quotes", {}).get("items", {})
    macro = context.get("macro", {}).get("items", {})
    us = _row_map(quotes.get("US", []))
    return {"us": us, "macro": macro}


def _market_context_lines(data: Dict[str, Any]) -> List[str]:
    bundle = _market_context_bundle(data)
    us = bundle["us"]
    macro = bundle["macro"]
    spy = us.get("S&P 500", {})
    rsp = us.get("Equal Weight S&P 500", {})
    iwm = us.get("Small Caps ETF", {})
    vix = us.get("VIX", {})
    tnx = us.get("US 10Y Yield", {})
    dollar = us.get("Dollar Index", {})
    hyg = us.get("High Yield Bond ETF", {})
    lqd = us.get("Investment Grade Bond ETF", {})
    t10y2y = macro.get("T10Y2Y", {})
    dgs10 = macro.get("DGS10", {})
    lines = [
        f"- 미국 주식: S&P 500 {_fmt_pct(spy.get('change_pct'))}, 동일가중 {_fmt_pct(rsp.get('change_pct'))}, 소형주 {_fmt_pct(iwm.get('change_pct'))}",
        f"- 변동성/금리: VIX {_fmt_num(vix.get('price'), 1)}, 10Y {_fmt_num(tnx.get('price'), 2)}%, 10Y-2Y {_fmt_num(t10y2y.get('value'), 2)}%",
        f"- 달러/크레딧: Dollar {_fmt_pct(dollar.get('change_pct'))}, HYG {_fmt_pct(hyg.get('change_pct'))}, LQD {_fmt_pct(lqd.get('change_pct'))}",
    ]
    if dgs10:
        lines.append(f"- 기준금리 참고: US 10Y Treasury {_fmt_num(dgs10.get('value'), 2)}% ({dgs10.get('source', 'FRED')})")
    return lines


def _liquidity_derivatives_lines(data: Dict[str, Any]) -> List[str]:
    stablecoins = data.get("stablecoins", {})
    btc = _derivative(data, "BTCUSDT")
    eth = _derivative(data, "ETHUSDT")
    sol = _derivative(data, "SOLUSDT")
    lines = []
    if stablecoins.get("ok"):
        lines.append(
            f"- 스테이블코인 공급: {_fmt_money(stablecoins.get('total_circulating_usd'))} "
            f"(7d {_fmt_pct(stablecoins.get('change_pct_7d'))}, 30d {_fmt_pct(stablecoins.get('change_pct_30d'))})"
        )
    else:
        lines.append(f"- 스테이블코인 공급: 수집 실패 ({stablecoins.get('error', 'unknown')})")
    for row in [btc, eth, sol]:
        if row.get("ok"):
            lines.append(
                f"- {row.get('label')}: 펀딩비 8h {_fmt_pct(row.get('funding_rate_pct_8h'))}, "
                f"미결제약정 3d {_fmt_pct(row.get('open_interest_value_change_pct_3d'))}, "
                f"롱/숏 { _fmt_num(row.get('long_short_ratio'), 2) }"
            )
        elif row:
            lines.append(f"- {row.get('label')}: 파생지표 수집 실패 ({row.get('error')})")
    return lines


def _sentiment_onchain_lines(data: Dict[str, Any]) -> List[str]:
    fg = _fear_greed_latest(data)
    onchain = data.get("onchain", {})
    items = onchain.get("items", {})
    lines = [
        (
            f"- Alternative.me Fear & Greed: {_fmt_num(fg.get('value'), 0)}"
            f"({fg.get('classification', 'n/a')}, {fg.get('date', 'latest')})"
        )
    ]
    lines.append("- 무료 온체인 데이터: Coin Metrics Community API")
    for asset in ["btc", "eth"]:
        row = items.get(asset, {})
        if not row:
            continue
        if row.get("ok"):
            label = row.get("label", asset.upper())
            metrics = row.get("metrics", {})
            if asset == "btc":
                lines.append(
                    f"- {label}: 활성주소 7d {_fmt_pct(metrics.get('AdrActCnt', {}).get('change_pct_7d'))}, "
                    f"거래 7d {_fmt_pct(metrics.get('TxCnt', {}).get('change_pct_7d'))}, "
                    f"수수료 7d {_fmt_pct(metrics.get('FeeTotNtv', {}).get('change_pct_7d'))}, "
                    f"해시레이트 7d {_fmt_pct(metrics.get('HashRate', {}).get('change_pct_7d'))}"
                )
            else:
                lines.append(
                    f"- {label}: 활성주소 7d {_fmt_pct(metrics.get('AdrActCnt', {}).get('change_pct_7d'))}, "
                    f"거래 7d {_fmt_pct(metrics.get('TxCnt', {}).get('change_pct_7d'))}, "
                    f"수수료 7d {_fmt_pct(metrics.get('FeeTotNtv', {}).get('change_pct_7d'))}"
                )
        else:
            lines.append(f"- {row.get('label', asset.upper())}: 수집 실패 ({row.get('error')})")
    return lines


def _news_title_ko(title: str) -> str:
    lower = title.lower()
    if "etf" in lower and ("bitcoin" in lower or "ether" in lower or "ethereum" in lower):
        return "현물 ETF 수급이 BTC/ETH 단기 방향의 핵심 변수로 부각"
    if "fed" in lower or "rate" in lower or "inflation" in lower:
        return "미국 금리/물가 뉴스가 코인 위험자산 심리에 영향"
    if "sec" in lower or "regulation" in lower or "lawsuit" in lower:
        return "규제 뉴스가 특정 코인과 거래소 심리에 영향"
    if "liquidation" in lower or "leverage" in lower:
        return "레버리지 청산 이슈로 단기 변동성 확대 가능"
    if "stablecoin" in lower:
        return "스테이블코인 유동성 변화가 코인 시장 체력 변수"
    return title


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


def _select_core_evidence(dashboard: Dict[str, Any]) -> List[str]:
    negatives = dashboard.get("negatives", [])
    priority_words = ["BTC 7일", "스테이블코인", "미결제약정", "펀딩비", "전체 코인 시총", "Fear & Greed", "MVRV", "ETH", "도미넌스", "차트", "RSI", "SMA"]
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
    if item.startswith("BTC 24시간"):
        return f"{item} -> 대표 코인이 약하면 알트코인은 더 크게 흔들리기 쉽습니다."
    if item.startswith("BTC 7일"):
        return f"{item} -> 하루짜리 흔들림이 아니라 추세 약화인지 확인해야 합니다."
    if "전체 코인 시총" in item:
        return f"{item} -> 특정 코인 문제가 아니라 시장 전체 자금 흐름이 약한 신호입니다."
    if "스테이블코인" in item:
        return f"{item} -> 스테이블코인은 코인 시장의 대기자금이라 줄면 매수 체력이 약해집니다."
    if "펀딩비" in item:
        return f"{item} -> 선물 롱이 많아지면 작은 하락에도 청산 변동성이 커질 수 있습니다."
    if "미결제약정" in item:
        return f"{item} -> 가격은 약한데 레버리지만 쌓이면 급락/급등 변동성이 커집니다."
    if "롱/숏" in item:
        return f"{item} -> 한쪽 포지션이 과하게 몰리면 반대 방향 청산 위험이 커집니다."
    if "Fear & Greed" in item:
        return f"{item} -> 심리가 한쪽으로 쏠리면 반대 방향 변동성이 커집니다."
    if "차트" in item or "RSI" in item or "SMA" in item:
        return f"{item} -> 가격 구조와 추세 정렬을 보는 보조 확인 신호입니다."
    if "MVRV" in item:
        return f"{item} -> 온체인 기준으로 평균 매입가 대비 가격 부담을 보는 지표입니다."
    if "ETH가 BTC" in item:
        return f"{item} -> 알트코인으로 위험을 더 가져가려는 심리가 약하다는 뜻입니다."
    if "VIX" in item:
        return f"{item} -> 주식 변동성이 높으면 코인도 같이 흔들릴 가능성이 큽니다."
    if "달러지수" in item:
        return f"{item} -> 달러가 강하면 비트코인과 알트코인 모두에 압력이 생기기 쉽습니다."
    if "10년물" in item or "10Y" in item:
        return f"{item} -> 금리가 높으면 위험자산 할인율 부담이 커집니다."
    if "동일가중" in item:
        return f"{item} -> 시장 폭이 넓어질수록 위험자산 전반에 힘이 붙기 쉽습니다."
    if "하이일드" in item:
        return f"{item} -> 크레딧 선호가 살아나야 코인 같은 고위험 자산에도 자금이 붙습니다."
    if "S&P 500" in item or "미국" in item:
        return f"{item} -> 주식 위험선호가 좋아질수록 코인에도 유리한 경우가 많습니다."
    return item


def _confidence_label(data: Dict[str, Any], dashboard: Dict[str, Any]) -> str:
    has_prices = bool(data.get("markets", {}).get("items"))
    has_global = bool(data.get("global", {}).get("ok"))
    has_market_context = bool(data.get("market_context", {}).get("quotes", {}).get("items"))
    has_fng = bool(data.get("fear_greed", {}).get("items"))
    has_news = bool(data.get("news", {}).get("items"))
    has_stablecoins = bool(data.get("stablecoins", {}).get("ok"))
    has_derivatives = bool(data.get("derivatives", {}).get("ok"))
    has_onchain = bool(data.get("onchain", {}).get("ok"))
    coverage = sum([has_prices, has_global, has_market_context, has_fng, has_news, has_stablecoins, has_derivatives, has_onchain])
    if has_onchain and has_market_context and coverage >= 7 and abs(dashboard.get("score", 0)) >= 3:
        return "높음"
    if coverage >= 6:
        return "보통+"
    if coverage >= 4:
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
            return None
    return None


def _freshness_profile(data: Dict[str, Any]) -> Dict[str, Any]:
    now = dt.datetime.now(dt.timezone.utc)
    market_ages: List[float] = []
    for row in data.get("markets", {}).get("items", []):
        parsed = _parse_any_datetime(row.get("last_updated"))
        if parsed is not None:
            market_ages.append((now - parsed).total_seconds() / 3600.0)
    global_updated = _parse_any_datetime(data.get("global", {}).get("updated_at"))
    if global_updated is not None:
        market_ages.append((now - global_updated).total_seconds() / 3600.0)
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
        "prices": bool(data.get("markets", {}).get("items")),
        "global": bool(data.get("global", {}).get("ok")),
        "market_context": bool(data.get("market_context", {}).get("quotes", {}).get("items")),
        "stablecoins": bool(data.get("stablecoins", {}).get("ok")),
        "derivatives": bool(data.get("derivatives", {}).get("ok")),
        "onchain": bool(data.get("onchain", {}).get("ok")),
        "fear_greed": bool(data.get("fear_greed", {}).get("items")),
        "news": bool(data.get("news", {}).get("items")),
        "freshness": freshness["score"] >= 70,
    }
    critical = ["prices", "global", "market_context", "stablecoins", "derivatives", "onchain"]
    support = ["fear_greed", "news"]
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
    base_confidence = quality["score"] * 0.42 + min(abs(score) * 7.0, 25.0) + min(evidence_count * 2.5, 15.0)
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
    elif quality["score"] < 60:
        trade_mode = "관망"
        position_size = 0
    elif verdict == "위험 우위":
        trade_mode = "현금 우선"
        position_size = 0
    elif quality["score"] >= 80 and verdict == "중립~우호" and base_confidence >= 65:
        trade_mode = "실전 후보"
        position_size = 100
    elif base_confidence >= 45:
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
    elif reliability_state == "미검증":
        if int(reliability.get("sample_count", 0) or 0) < 10 and trade_mode == "실전 후보":
            trade_mode = "조건부"
        if int(reliability.get("sample_count", 0) or 0) < 10:
            position_size = int(round(position_size * 0.8))
    elif reliability_state == "안정":
        position_size = int(round(position_size * float(reliability.get("position_multiplier_cap", 1.0) or 1.0)))
    position_size = max(0, min(100, position_size))

    if verdict == "위험 우위":
        entry = "BTC 7일 흐름이 다시 강해지고 스테이블코인 공급이 늘어날 때까지 신규 진입 보류"
        invalidation = "BTC 약세 속 펀딩비/OI가 다시 과열되거나, 스테이블코인 공급이 추가 감소하면 즉시 대기"
    elif verdict == "주의":
        entry = "BTC와 ETH가 동반 회복하고, 펀딩비와 OI가 과열되지 않을 때만 분할 검토"
        invalidation = "ETH가 BTC보다 약해지거나 VIX/달러/금리가 동시에 악화되면 분할 중단"
    else:
        entry = "데이터 품질이 높고 BTC/ETH 상대강도가 개선될 때만 작은 비중으로 접근"
        invalidation = "대기자금이 줄거나 고베타 알트가 BTC보다 계속 약하면 비중 확대 중지"
    if trade_mode == "실전 후보":
        daily_loss_limit_pct = 1.0
    elif trade_mode == "조건부":
        daily_loss_limit_pct = 0.75
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


def _final_checkpoints(data: Dict[str, Any]) -> List[str]:
    dashboard = _risk_dashboard(data)
    rows = _row_map(data.get("markets", {}).get("items", []))
    btc = rows.get("bitcoin", {})
    eth = rows.get("ethereum", {})
    flow = _flow_state(data)
    btc_7d = btc.get("change_pct_7d")
    eth_vs_btc = None
    if isinstance(eth.get("change_pct_7d"), (int, float)) and isinstance(btc_7d, (int, float)):
        eth_vs_btc = eth.get("change_pct_7d") - btc_7d
    lines = ["- 1순위는 진입보다 리스크 한도입니다. 코인은 하루 변동성이 커서 총 투자금과 손절 기준을 먼저 정합니다."]
    if dashboard.get("score", 0) >= 3:
        lines.append("- 신규 진입은 BTC 7일 수익률이 개선되고 스테이블코인 7일 공급이 플러스로 돌아설 때까지 작게 나눕니다.")
    else:
        lines.append("- 메이저 중심으로 분할 접근하되, BTC가 다시 7일 기준 약세로 꺾이면 속도를 줄입니다.")
    if isinstance(eth_vs_btc, (int, float)):
        if eth_vs_btc > 2:
            lines.append("- ETH가 BTC보다 강하면 알트 비중을 조금 열 수 있지만, SOL/LINK 같은 유동성 큰 종목부터 봅니다.")
        else:
            lines.append("- ETH가 BTC보다 약하면 알트 확장은 보류하고 BTC/현금 중심으로 봅니다.")
    if isinstance(flow.get("stable_7d"), (int, float)) and flow["stable_7d"] < 0:
        lines.append("- 스테이블코인 공급이 줄어드는 동안은 급등 추격보다 눌림 확인이 낫습니다.")
    else:
        lines.append("- 스테이블코인 공급이 늘고 펀딩비가 과열되지 않을 때만 분할 진입 신뢰도가 올라갑니다.")
    return lines[:5]


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


def _openai_failed_softly(exc: Exception) -> bool:
    text = str(exc).lower()
    return "429" in text or "rate limit" in text or "rate_limit_exceeded" in text


def _trade_engine_lines(data: Dict[str, Any], dashboard: Dict[str, Any], ai_signal: Optional[Dict[str, Any]] = None) -> List[str]:
    engine = _trade_engine(data, dashboard, ai_signal=ai_signal)
    critical_missing = engine.get("critical_missing", [])
    missing = engine.get("quality_missing", [])
    critical_labels = {
        "prices": "코인 가격",
        "global": "전체 시총",
        "market_context": "미국 시장 컨텍스트",
        "stablecoins": "스테이블코인",
        "derivatives": "파생시장",
        "onchain": "온체인",
        "fear_greed": "심리",
        "news": "뉴스",
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
    return build_reliability_guard(calibration, context="crypto")


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
    if asset_kind == "btc":
        base_stop = 3.8
        base_pullback = 0.9
        reward_floor = 2.1
        stop_cap = 10.0
    else:
        base_stop = 4.8
        base_pullback = 1.2
        reward_floor = 2.2
        stop_cap = 12.0
    stop_pct = min(
        stop_cap,
        max(
            base_stop,
            avg_abs_return_pct * (0.5 if asset_kind == "btc" else 0.45),
            (1.1 if confidence_score >= 65 else 1.6) + (0.2 * max(confidence_score - 50, 0) / 10.0),
        ),
    )
    if expected_return_pct > 0:
        entry_offset_pct = base_pullback if confidence_score >= 65 else base_pullback + (0.5 if asset_kind == "btc" else 0.8)
    else:
        entry_offset_pct = base_pullback + (1.2 if asset_kind == "btc" else 1.6)
    if trade_mode in {"관망", "현금 우선"}:
        entry_offset_pct += 0.8 if asset_kind == "btc" else 1.2
        stop_pct += 0.6 if asset_kind == "btc" else 0.8
    take_pct = max(
        stop_pct * reward_floor,
        abs(expected_return_pct) * (2.4 if asset_kind == "btc" else 2.7) + (1.5 if asset_kind == "btc" else 2.0),
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
    rows = _row_map(data.get("markets", {}).get("items", []))
    plan_assets = [
        ("BTC", rows.get("Bitcoin", {}).get("price"), "기준자산"),
        ("ETH", rows.get("Ethereum", {}).get("price"), "알트 확인"),
    ]
    lines = [
        "[walk-forward 보정]",
        f"- 샘플 개수: {calibration['sample_count']}",
        f"- 회귀모형: 기대수익 = {model.get('slope', 0.0):+.4f} * score + {model.get('intercept', 0.0):+.4f}",
        f"- 방향 적중률: {((calibration.get('walk_forward_direction_hit_rate') or 0.0) * 100):.1f}%",
        f"- walk-forward MAE: {calibration['walk_forward_mae']:.2f}%" if calibration.get("walk_forward_mae") is not None else "- walk-forward MAE: n/a",
        f"- 현재 점수 기준 기대수익: {expected_return_pct:+.2f}%",
        "",
        "[매매 포맷]",
    ]
    for symbol, price, label in plan_assets:
        asset_kind = "btc" if symbol == "BTC" else "eth"
        trade = _price_trade_levels(
            price,
            expected_return_pct=expected_return_pct,
            confidence_score=confidence_score,
            avg_abs_return_pct=float(calibration.get("avg_abs_return_pct", 0.0) or 0.0),
            asset_kind=asset_kind,
            trade_mode=str(engine.get("trade_mode", "")),
        )
        if price is None or trade["entry_price"] is None:
            lines.append(f"- {label}: 가격 수집 실패")
            continue
        lines.append(
            f"- {label} ({symbol}): 현재가 {_fmt_money(price)}, "
            f"진입가 {_fmt_money(trade['entry_price'])}, 손절가 {_fmt_money(trade['stop_price'])}, "
            f"익절가 {_fmt_money(trade['take_price'])}, 비중 {final_size}%"
        )
    lines.append(f"- 비중 해석: 기본 엔진 {engine.get('position_size', 0)}%에 보정 멀티플 {float(calibration.get('recommended_position_multiplier', 0.0) or 0.0):.2f}를 곱한 값입니다.")
    if final_size == 0:
        lines.append("- 실행 해석: 아직은 실전 진입보다 관망/현금 유지가 우선입니다.")
    else:
        lines.append("- 실행 해석: BTC 중심 분할 진입만 허용하고, ETH는 상대강도 확인 후 붙입니다.")
    return lines


def _candidate_score(label: str, row: Dict[str, Any], btc: Dict[str, Any], dashboard: Dict[str, Any], data: Dict[str, Any]) -> Dict[str, Any]:
    score = 0.0
    reasons = []
    cautions = []
    risk_score = dashboard.get("score", 0)
    change_24h = row.get("change_pct_24h")
    change_7d = row.get("change_pct_7d")
    change_30d = row.get("change_pct_30d")
    btc_7d = btc.get("change_pct_7d")
    stable_7d = data.get("stablecoins", {}).get("change_pct_7d")

    if label in {"Bitcoin", "Ethereum"}:
        score += 1.5
        reasons.append("유동성과 정보 신뢰도가 높아 기준 자산으로 보기 좋음")
    if label == "Bitcoin" and risk_score >= 3:
        score += 1.0
        reasons.append("불안한 장에서는 알트보다 방어력이 상대적으로 나음")
    if label == "Ethereum":
        if isinstance(change_7d, (int, float)) and isinstance(btc_7d, (int, float)) and change_7d > btc_7d + 2:
            score += 1.2
            reasons.append("BTC보다 7일 상대강도가 좋아 알트 확장의 첫 확인 후보")
        elif isinstance(change_7d, (int, float)) and isinstance(btc_7d, (int, float)):
            cautions.append("BTC 대비 상대강도가 아직 약하면 알트 확장 신호로 보기 어려움")
    if label in {"Solana", "Chainlink", "Avalanche", "Sui"}:
        if risk_score <= 3:
            score += 0.7
            reasons.append("위험선호 회복 때 움직임이 빠른 유동성 알트 후보")
        else:
            score -= 0.8
            cautions.append("위험 점수가 높을 때는 고베타 알트 비중 확대를 늦추는 편이 좋음")
    if isinstance(change_24h, (int, float)) and isinstance(change_7d, (int, float)):
        if change_24h > 0 and change_7d > -3:
            score += 0.7
            reasons.append(f"24h {_fmt_pct(change_24h)}, 7d {_fmt_pct(change_7d)}로 단기 회복 확인")
        elif change_7d < -10:
            score -= 0.8
            cautions.append(f"7d {_fmt_pct(change_7d)}라 하락 추세가 아직 큼")
    if isinstance(change_30d, (int, float)) and change_30d < -25:
        score -= 0.4
        cautions.append(f"30d {_fmt_pct(change_30d)}로 중기 낙폭이 커서 변동성 주의")
    if isinstance(stable_7d, (int, float)) and stable_7d < 0 and label not in {"Bitcoin", "Ethereum"}:
        score -= 0.4
        cautions.append("대기자금이 줄 때 알트 반등은 지속성이 약할 수 있음")
    return {"label": label, "score": score, "reasons": reasons[:2], "cautions": cautions[:2], "row": row}


def _simple_coin_name(label: str) -> str:
    names = {
        "Bitcoin": "비트코인(BTC)",
        "Ethereum": "이더리움(ETH)",
        "Solana": "솔라나(SOL)",
        "Chainlink": "체인링크(LINK)",
        "Avalanche": "아발란체(AVAX)",
        "Sui": "수이(SUI)",
        "XRP": "리플(XRP)",
        "BNB": "BNB",
        "Dogecoin": "도지코인(DOGE)",
        "Cardano": "카르다노(ADA)",
    }
    return names.get(label, label)


def _plain_coin_role(label: str) -> str:
    roles = {
        "Bitcoin": "시장의 기준자산입니다. BTC가 버티지 못하면 대부분의 알트는 더 크게 흔들릴 가능성이 큽니다.",
        "Ethereum": "알트 시장의 체력을 보는 1차 확인 자산입니다. ETH가 BTC보다 강해질 때 알트 확장 신뢰도가 올라갑니다.",
        "Solana": "고베타 성장 알트입니다. 장이 좋아질 때 빠르지만, 장이 나쁘면 손실도 빠르게 커질 수 있습니다.",
        "Chainlink": "인프라 성격의 대형 알트입니다. 급등 테마보다 시장 회복 확인용 후보에 가깝습니다.",
        "Avalanche": "생태계형 알트입니다. 유동성이 좋아질 때만 조건부로 볼 후보입니다.",
        "Sui": "성장 기대가 큰 고변동성 알트입니다. 시장 체력이 약하면 추격보다 보류가 맞습니다.",
    }
    return roles.get(label, "유동성과 변동성을 같이 확인해야 하는 알트 후보입니다.")


def _action_label(item: Dict[str, Any], dashboard: Dict[str, Any], data: Dict[str, Any]) -> str:
    label = item["label"]
    stable_7d = data.get("stablecoins", {}).get("change_pct_7d")
    risk_score = dashboard.get("score", 0)
    row = item["row"]
    change_7d = row.get("change_pct_7d")
    if label == "Bitcoin":
        if risk_score >= 4:
            return "관찰 우선"
        return "소액 분할 검토"
    if label == "Ethereum":
        btc = _row_map(data.get("markets", {}).get("items", [])).get("bitcoin", {})
        btc_7d = btc.get("change_pct_7d")
        if isinstance(change_7d, (int, float)) and isinstance(btc_7d, (int, float)) and change_7d > btc_7d:
            return "조건부 분할 검토"
        return "회복 확인 전 보류"
    if risk_score >= 3 or (isinstance(stable_7d, (int, float)) and stable_7d < 0):
        return "지금은 보류"
    return "소액 관찰"


def _entry_condition(label: str, data: Dict[str, Any]) -> str:
    rows = _row_map(data.get("markets", {}).get("items", []))
    btc = rows.get("bitcoin", {})
    eth = rows.get("ethereum", {})
    stable_7d = data.get("stablecoins", {}).get("change_pct_7d")
    btc_7d = btc.get("change_pct_7d")
    eth_7d = eth.get("change_pct_7d")
    if label == "Bitcoin":
        return "BTC 7일 수익률이 개선되고, 스테이블코인 공급 감소가 멈추는지 확인"
    if label == "Ethereum":
        return "ETH 7일 수익률이 BTC보다 강해지는지 확인"
    if isinstance(stable_7d, (int, float)) and stable_7d < 0:
        return "스테이블코인 7일 공급이 플러스로 돌아선 뒤 확인"
    if isinstance(eth_7d, (int, float)) and isinstance(btc_7d, (int, float)) and eth_7d <= btc_7d:
        return "ETH가 BTC보다 강해진 뒤 확인"
    return "BTC/ETH가 동반 회복하고 펀딩비가 과열되지 않을 때만 확인"


def _sector_recommendations(data: Dict[str, Any]) -> List[str]:
    dashboard = _risk_dashboard(data)
    rows = _row_map(data.get("markets", {}).get("items", []))
    score = dashboard.get("score", 0)
    btc = rows.get("bitcoin", {})
    eth = rows.get("ethereum", {})
    stable_7d = data.get("stablecoins", {}).get("change_pct_7d")
    candidates = []
    for coin_id, row in rows.items():
        if row.get("label"):
            candidates.append(_candidate_score(row["label"], row, btc, dashboard, data))
    candidates.sort(key=lambda item: item["score"], reverse=True)
    preferred = [item for item in candidates if item["label"] in {"Bitcoin", "Ethereum"}][:2]
    conditional = [
        item for item in candidates
        if item["label"] not in {"Bitcoin", "Ethereum", "Dogecoin", "Cardano"} and item["score"] >= 0.4
    ][:2]
    avoid = [item for item in sorted(candidates, key=lambda item: item["score"])[:3] if item["score"] < 0.5]

    if score >= 3 or (isinstance(stable_7d, (int, float)) and stable_7d < 0):
        lines = ["- 오늘 결론: 공격적으로 살 장은 아닙니다. 현금 여력을 남기고 BTC/ETH만 먼저 확인합니다."]
    else:
        lines = ["- 오늘 결론: 메이저 코인 중심의 작은 분할은 가능하지만, 알트는 조건부로만 봅니다."]
    for item in preferred:
        row = item["row"]
        simple_name = _simple_coin_name(item["label"])
        lines.append(
            f"- {_action_label(item, dashboard, data)}: {simple_name} "
            f"(24h {_fmt_pct(row.get('change_pct_24h'))}, 7d {_fmt_pct(row.get('change_pct_7d'))})"
        )
        lines.append(f"  이유: {_plain_coin_role(item['label'])}")
        lines.append(f"  볼 조건: {_entry_condition(item['label'], data)}")
    if conditional:
        names = ", ".join(_simple_coin_name(item["label"]) for item in conditional)
        if score >= 3 or (isinstance(stable_7d, (int, float)) and stable_7d < 0):
            lines.append(f"- 알트 후보: {names}. 지금 사자는 뜻이 아니라, 시장 체력이 회복되면 먼저 볼 목록입니다.")
        else:
            lines.append(f"- 알트 후보: {names}. ETH가 BTC보다 강한 날에만 소액으로 관찰합니다.")
    if isinstance(eth.get("change_pct_7d"), (int, float)) and isinstance(btc.get("change_pct_7d"), (int, float)):
        gap = eth["change_pct_7d"] - btc["change_pct_7d"]
        if gap < 0:
            lines.append(f"- 알트 판단: ETH가 BTC보다 7일 기준 {abs(gap):.2f}%p 더 약합니다. 이 상태에서는 알트 비중 확대를 서두르지 않습니다.")
        else:
            lines.append(f"- 알트 판단: ETH가 BTC보다 7일 기준 {_fmt_pct(gap)}p 강합니다. 이 흐름이 유지되면 알트 관찰 신뢰도가 올라갑니다.")
    if score >= 4:
        lines.append("- 실행 기준: 마음에 드는 후보가 있어도 첫 진입은 작게 하고, BTC/ETH가 동시에 회복될 때만 비중을 늘립니다.")
    else:
        lines.append("- 실행 기준: BTC/ETH 중심으로 시작하고, ETH 상대강도와 스테이블코인 공급이 같이 좋아질 때 알트를 봅니다.")
    if avoid:
        lines.append("- 오늘 보류: " + ", ".join(_simple_coin_name(item["label"]) for item in avoid) + ". 약한 장에서 먼저 손대기보다 회복 확인이 필요합니다.")
    else:
        lines.append("- 보류/주의: 저유동성 알트, 급등 밈코인, 레버리지 선물")
    return lines


def _plain_verdict(dashboard: Dict[str, Any]) -> str:
    verdict = dashboard.get("verdict")
    if verdict == "위험 우위":
        return "쉽게 말해, 지금은 수익 기회보다 크게 흔들릴 가능성을 더 먼저 봐야 하는 장입니다."
    if verdict == "주의":
        return "쉽게 말해, 반등은 가능하지만 알트까지 자신 있게 따라붙기엔 확인할 게 남아 있습니다."
    return "쉽게 말해, 큰 과열/붕괴 신호는 제한적이지만 코인은 변동성이 커서 분할 접근이 맞습니다."


def _regime_label(data: Dict[str, Any], dashboard: Optional[Dict[str, Any]] = None) -> str:
    dashboard = dashboard or _risk_dashboard(data)
    score = float(dashboard.get("score", 0) or 0)
    btc_7d = None
    for row in data.get("markets", {}).get("items", []):
        if row.get("label") == "Bitcoin":
            try:
                btc_7d = float(row.get("change_pct_7d"))
            except (TypeError, ValueError):
                btc_7d = None
            break
    stable_7d = data.get("stablecoins", {}).get("change_pct_7d")
    if score >= 5:
        return "고변동성/방어"
    if score >= 3:
        return "주의/혼조"
    if isinstance(btc_7d, (int, float)) and btc_7d >= 3 and isinstance(stable_7d, (int, float)) and stable_7d >= 0:
        return "상승 우호"
    if score <= 1:
        return "횡보/중립"
    return "혼조"


def _decision_brief(dashboard: Dict[str, Any], engine: Dict[str, Any]) -> List[str]:
    verdict = dashboard.get("verdict", "중립~우호")
    score = dashboard.get("score", 0)
    confidence = engine.get("confidence_score", 0)
    trade_mode = engine.get("trade_mode", "관망")
    position_size = engine.get("position_size", 0)
    if trade_mode == "현금 우선" or verdict == "위험 우위":
        action = "오늘은 신규 매수보다 현금 비중 유지가 우선입니다."
    elif trade_mode == "실전 후보":
        action = "조건이 맞으면 BTC 중심 분할매수 후보로 볼 수 있습니다."
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


def _crypto_core_snapshot(data: Dict[str, Any]) -> Dict[str, Any]:
    rows = _row_map(data.get("markets", {}).get("items", []))
    btc = rows.get("bitcoin", {})
    eth = rows.get("ethereum", {})
    sol = rows.get("solana", {})
    global_data = data.get("global", {})
    market_context_rows = _row_map(data.get("market_context", {}).get("quotes", {}).get("items", {}).get("US", []))
    derivatives = data.get("derivatives", {}).get("items", {})
    btc_perp = derivatives.get("BTCUSDT", {})
    eth_perp = derivatives.get("ETHUSDT", {})
    technical = _technical_row_map(data)
    btc_tech = technical.get("Bitcoin", {})
    eth_tech = technical.get("Ethereum", {})
    fg = _fear_greed_latest(data)
    flow = _flow_state(data)
    return {
        "btc_price": btc.get("price"),
        "btc_24h": btc.get("change_pct_24h"),
        "btc_7d": btc.get("change_pct_7d"),
        "btc_30d": btc.get("change_pct_30d"),
        "eth_price": eth.get("price"),
        "eth_24h": eth.get("change_pct_24h"),
        "eth_7d": eth.get("change_pct_7d"),
        "eth_30d": eth.get("change_pct_30d"),
        "sol_7d": sol.get("change_pct_7d"),
        "btc_dom": global_data.get("btc_dominance_pct"),
        "eth_dom": global_data.get("eth_dominance_pct"),
        "global_24h": flow.get("global_24h"),
        "stable_7d": flow.get("stable_7d"),
        "stable_30d": flow.get("stable_30d"),
        "vix": market_context_rows.get("VIX", {}).get("price"),
        "us10y": market_context_rows.get("US 10Y Yield", {}).get("price"),
        "dxy": market_context_rows.get("Dollar Index", {}).get("price"),
        "hyg": market_context_rows.get("High Yield Bond ETF", {}).get("price"),
        "funding_btc": btc_perp.get("funding_rate_pct_8h"),
        "oi_btc_3d": btc_perp.get("open_interest_value_change_pct_3d"),
        "ls_btc": btc_perp.get("long_short_ratio"),
        "funding_eth": eth_perp.get("funding_rate_pct_8h"),
        "oi_eth_3d": eth_perp.get("open_interest_value_change_pct_3d"),
        "ls_eth": eth_perp.get("long_short_ratio"),
        "fng_value": fg.get("value"),
        "fng_classification": fg.get("classification"),
        "btc_active_7d": _onchain_metric_change_pct_7d(data, "btc", "AdrActCnt"),
        "btc_tx_7d": _onchain_metric_change_pct_7d(data, "btc", "TxCnt"),
        "btc_fee_7d": _onchain_metric_change_pct_7d(data, "btc", "FeeTotNtv"),
        "btc_hash_7d": _onchain_metric_change_pct_7d(data, "btc", "HashRate"),
        "eth_active_7d": _onchain_metric_change_pct_7d(data, "eth", "AdrActCnt"),
        "eth_tx_7d": _onchain_metric_change_pct_7d(data, "eth", "TxCnt"),
        "eth_fee_7d": _onchain_metric_change_pct_7d(data, "eth", "FeeTotNtv"),
        "btc_rsi14": btc_tech.get("rsi14"),
        "btc_sma20": btc_tech.get("sma20"),
        "btc_sma50": btc_tech.get("sma50"),
        "btc_sma200": btc_tech.get("sma200"),
        "btc_trend_state": btc_tech.get("trend_state"),
        "eth_rsi14": eth_tech.get("rsi14"),
        "eth_sma20": eth_tech.get("sma20"),
        "eth_sma50": eth_tech.get("sma50"),
        "eth_sma200": eth_tech.get("sma200"),
        "eth_trend_state": eth_tech.get("trend_state"),
    }


def _crypto_core_data_lines(snapshot: Dict[str, Any]) -> List[str]:
    return [
        f"- BTC/ETH: BTC {_fmt_money(snapshot.get('btc_price'))} ({_fmt_pct(snapshot.get('btc_24h'))}, 7d {_fmt_pct(snapshot.get('btc_7d'))}), ETH {_fmt_money(snapshot.get('eth_price'))} ({_fmt_pct(snapshot.get('eth_24h'))}, 7d {_fmt_pct(snapshot.get('eth_7d'))})",
        f"- 차트 추세: BTC {snapshot.get('btc_trend_state', 'n/a')} / RSI {_fmt_num(snapshot.get('btc_rsi14'), 1)} / SMA20 {_fmt_money(snapshot.get('btc_sma20'))}, ETH {snapshot.get('eth_trend_state', 'n/a')} / RSI {_fmt_num(snapshot.get('eth_rsi14'), 1)} / SMA20 {_fmt_money(snapshot.get('eth_sma20'))}",
        f"- 도미넌스/유동성: BTC 도미넌스 {_fmt_num(snapshot.get('btc_dom'), 1)}%, ETH 도미넌스 {_fmt_num(snapshot.get('eth_dom'), 1)}%, 스테이블코인 7d {_fmt_pct(snapshot.get('stable_7d'))}, 30d {_fmt_pct(snapshot.get('stable_30d'))}",
        f"- 파생시장: BTC funding {_fmt_pct(snapshot.get('funding_btc'))}, OI 3d {_fmt_pct(snapshot.get('oi_btc_3d'))}, 롱/숏 {_fmt_num(snapshot.get('ls_btc'), 2)}; ETH funding {_fmt_pct(snapshot.get('funding_eth'))}, OI 3d {_fmt_pct(snapshot.get('oi_eth_3d'))}, 롱/숏 {_fmt_num(snapshot.get('ls_eth'), 2)}",
        f"- 매크로: VIX {_fmt_num(snapshot.get('vix'), 1)}, US 10Y {_fmt_num(snapshot.get('us10y'), 2)}%, 달러지수 {_fmt_num(snapshot.get('dxy'), 1)}, HYG {_fmt_money(snapshot.get('hyg'))}",
        f"- 온체인: BTC 활성주소 {_fmt_pct(snapshot.get('btc_active_7d'))}, 거래 {_fmt_pct(snapshot.get('btc_tx_7d'))}, 수수료 {_fmt_pct(snapshot.get('btc_fee_7d'))}, 해시레이트 {_fmt_pct(snapshot.get('btc_hash_7d'))}; ETH 활성주소 {_fmt_pct(snapshot.get('eth_active_7d'))}, 거래 {_fmt_pct(snapshot.get('eth_tx_7d'))}, 수수료 {_fmt_pct(snapshot.get('eth_fee_7d'))}",
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
    core_data = _crypto_core_snapshot(data)
    core_evidence = _select_core_evidence(dashboard)[:4] or dashboard.get("negatives", [])[:4]
    buffers = [item for item in dashboard.get("positives", [])[:2] if item not in core_evidence]
    if not buffers and dashboard.get("positives"):
        buffers = dashboard.get("positives", [])[:1]
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
        "project_id": "crypto-agent",
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
            "model": calibration.get("model", {}),
        },
        "reliability": reliability,
        "core_data": core_data,
        "core_evidence": core_evidence,
        "buffers": buffers,
        "ai_signal": ai_payload,
        "decision_brief": _decision_brief(dashboard, engine),
    }


def _brief_report_lines(data: Dict[str, Any], dashboard: Dict[str, Any], engine: Dict[str, Any], ai_signal: Optional[Dict[str, Any]] = None) -> str:
    today = dt.datetime.now(REPORT_TIMEZONE).strftime("%Y-%m-%d")
    hybrid = _combine_verdicts(dashboard, ai_signal) if ai_signal else None
    confidence = engine.get("confidence_score", 0)
    if ai_signal and hybrid:
        ai_signal = dict(ai_signal)
        ai_signal["combined_score"] = hybrid["score"]
        ai_signal["rule_verdict"] = hybrid["rule_verdict"]
        ai_signal["rule_score"] = hybrid["rule_score"]
        ai_signal["ai_verdict"] = hybrid["ai_verdict"]
        ai_signal["ai_confidence"] = hybrid["ai_confidence"]
    core_evidence = _select_core_evidence(dashboard)[:3] or dashboard.get("negatives", [])[:3]
    buffers = [item for item in dashboard.get("positives", [])[:2] if item not in core_evidence]
    if not buffers and dashboard.get("positives"):
        buffers = dashboard.get("positives", [])[:1]
    regime = _regime_label(data, dashboard)
    freshness = engine.get("freshness", {})
    quality_score = engine.get("quality_score", 0)
    core_data = _crypto_core_snapshot(data)
    lines = [
        f"[{today} 코인 시장 브리핑]",
        "",
        (
            f"판단: {hybrid['verdict']} | 룰 {dashboard['verdict']}({dashboard['score']}) | "
            f"실행 {engine.get('trade_mode', '관망')} {engine.get('position_size', 0)}% | "
            f"데이터 품질: {quality_score}/100 | 신뢰도: {confidence}/100"
            if hybrid
            else f"판단: {dashboard['verdict']} | 실행 {engine.get('trade_mode', '관망')} {engine.get('position_size', 0)}% | "
                 f"데이터 품질: {quality_score}/100 | 신뢰도: {confidence}/100 | 룰 점수 {dashboard['score']}"
        ),
        f"시장 국면: {regime} | 데이터 신선도: {freshness.get('score', 0)}/100",
        f"백테스트 상태: {engine.get('calibration_state', '미검증')} | 표본 {engine.get('calibration_sample_count', 0)} | 비중 상한 x{engine.get('calibration_position_cap', 1.0):.2f}",
        _plain_verdict(dashboard),
        "",
        "[핵심 데이터]",
        *_crypto_core_data_lines(core_data),
        "",
        "[차트 분석]",
        *_technical_lines(data),
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


def build_crypto_local_report(data: Dict[str, Any], ai_signal: Optional[Dict[str, Any]] = None, concise: bool = False) -> str:
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
    core_evidence = _select_core_evidence(dashboard)
    positives = dashboard.get("positives", [])[:2]
    buffers = [item for item in positives if item not in core_evidence]
    if not core_evidence and positives:
        core_evidence = positives[:2]
        buffers = [item for item in positives[2:] if item not in core_evidence]
    lines = [
        f"[{today} 코인 시장 브리핑]",
        "",
        (
            f"판단: {hybrid['verdict']} | 룰 {dashboard['verdict']}({dashboard['score']}) | "
            f"실행 {engine.get('trade_mode', '관망')} {engine.get('position_size', 0)}% | "
            f"신뢰도: {confidence}"
            if hybrid
            else f"판단: {dashboard['verdict']} | 실행 {engine.get('trade_mode', '관망')} {engine.get('position_size', 0)}% | "
                 f"신뢰도: {confidence} | 룰 점수 {dashboard['score']}"
        ),
        _plain_verdict(dashboard),
        f"요약: {dashboard['stance']}",
        "",
        *_trade_engine_lines(data, dashboard, ai_signal=ai_signal if ai_signal else None),
        "",
        *_trade_plan_lines(data, dashboard, engine),
        "",
        "[차트 분석]",
        *_technical_lines(data),
        "",
        "[핵심 투자 판단 근거]",
        *(f"- {_plain_reason(item)}" for item in core_evidence),
        *(f"- 완충 요인: {_plain_reason(item)}" for item in buffers),
        *(["", "[AI 보조 판단]", *_ai_section_lines(ai_signal, hybrid)] if ai_signal and hybrid else []),
        "",
        "[코인 시장 대시보드]",
        *_market_snapshot_lines(data),
        "",
        "[추세 점검]",
        *_trend_lines(data),
        "",
        "[거시 시장 컨텍스트]",
        *_market_context_lines(data),
        "",
        "[유동성·파생시장]",
        *_liquidity_derivatives_lines(data),
        "",
        "[심리·온체인 점검]",
        *_sentiment_onchain_lines(data),
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


def build_crypto_ai_report(data: Dict[str, Any], api_key: str, model: str) -> str:
    try:
        prompt = (
            "너는 개인 투자 참고용 코인 시장 브리핑 작성자다. "
            "제공된 JSON 데이터만 근거로 한국어 리포트를 작성해라. "
            "없는 숫자를 지어내지 말고, 데이터 수집 실패는 명시해라. "
            "온체인 데이터는 Coin Metrics Community API의 무료 지표를 기준으로 읽어라. "
            "투자 조언/매수매도 지시가 아니라 참고용 요약으로 써라. "
            "텔레그램에서 읽기 좋게 900~1400자 안팎으로 압축해라. "
            "전문 지표는 쓰되 초보자도 이해하게 쉬운 말로 풀어써라. "
            "결론을 먼저 쓰고, 핵심 근거는 최대 4개만 남겨라. "
            "BTC/ETH 가격 흐름, 전체 시총, BTC 도미넌스, 스테이블코인 공급, Binance 선물 펀딩비/미결제약정/롱숏비, 미국 주식/변동성/금리/달러/크레딧 컨텍스트, Alternative.me Fear & Greed, 무료 온체인 지표, 최신 뉴스를 연결해서 분석해라. "
            "상승 조건과 하방 위험 조건을 분리하고, 확정적 예측 대신 조건부 판단으로 써라. "
            "마지막에는 분할매수/관망/현금비중/리스크관리 관점으로 쉽게 정리해라. "
            "실전 매매 엔진처럼 데이터 품질, 신뢰도, 실행 모드, 진입 조건, 무효화 조건을 분명히 밝혀라. "
            "관심 후보 섹션은 반드시 초보자도 이해하게 써라. 코인 이름만 나열하지 말고 오늘 결론, 후보별 역할, 볼 조건, 보류 조건을 나눠라. "
            "예: '소액 분할 검토: 비트코인(BTC) - 시장의 기준자산. 볼 조건: BTC 7일 흐름 개선과 스테이블코인 공급 감소 중단.' "
            "예: '회복 확인 전 보류: 이더리움(ETH) - 알트 체력 확인 자산. 볼 조건: ETH가 BTC보다 강해질 때.' "
            "알트 후보는 지금 사라는 뜻이 아니라 BTC/ETH와 유동성이 회복될 때 볼 목록이라고 분명히 써라. "
            "형식: 판단, 왜 그렇게 보나, 시장 상태, 유동성과 파생시장, 심리와 온체인, 오늘 신경 쓸 뉴스, 내 대응 가이드, 관심 후보, 주의문."
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
            return build_crypto_local_report(data)
        return text
    except Exception:
        return build_crypto_local_report(data)


def build_crypto_report(
    data: Dict[str, Any],
    api_key: str = "",
    model: str = "gpt-4.1-mini",
    concise: bool = False,
    return_snapshot: bool = False,
):
    dashboard = _risk_dashboard(data)
    ai_signal = build_ai_signal(data, api_key=api_key, model=model, dashboard=dashboard) if api_key else None
    report = build_crypto_local_report(data, ai_signal=ai_signal, concise=concise)
    if return_snapshot:
        snapshot = build_decision_snapshot(data, ai_signal=ai_signal, dashboard=dashboard)
        return report, snapshot
    return report
