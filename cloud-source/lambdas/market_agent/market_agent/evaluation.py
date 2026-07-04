import datetime as dt
import json
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple

from .config import PERFORMANCE_DIR, REPORT_TIMEZONE
from .report import _risk_dashboard, _trade_engine


PROJECT_ID = "market-agent"
TARGET_FUTURE_GAP_HOURS = 24
FUTURE_GAP_TOLERANCE_HOURS = 3
STRATEGY_PROXY_LABELS = (
    ("US", "S&P 500"),
    ("Korea", "KOSPI"),
)
MARKET_BENCHMARK_WEIGHTS = {"left_weight": 0.5, "right_weight": 0.5, "method": "균등 비중"}
ROUND_TRIP_COST_PCT = 0.20
SLIPPAGE_PCT = 0.10
POSITION_CHANGE_COST_PCT = (ROUND_TRIP_COST_PCT + SLIPPAGE_PCT) / 2.0


def _parse_generated_at(raw: Dict[str, Any]) -> Optional[dt.datetime]:
    value = raw.get("generated_at")
    if not value:
        return None
    try:
        parsed = dt.datetime.fromisoformat(value)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=REPORT_TIMEZONE)
    return parsed


def _load_dynamodb_history(table_name: str) -> List[Dict[str, Any]]:
    try:
        import boto3  # type: ignore
        from boto3.dynamodb.conditions import Key  # type: ignore
    except Exception:
        return []

    session = boto3.session.Session()
    table = session.resource("dynamodb").Table(table_name)
    records: List[Dict[str, Any]] = []
    response = table.query(
        KeyConditionExpression=Key("project_id").eq(PROJECT_ID),
    )
    items = response.get("Items", [])
    while response.get("LastEvaluatedKey"):
        response = table.query(
            KeyConditionExpression=Key("project_id").eq(PROJECT_ID),
            ExclusiveStartKey=response["LastEvaluatedKey"],
        )
        items.extend(response.get("Items", []))
    for item in items:
        if item.get("record_type") != "report":
            continue
        generated_at = _parse_generated_at(item)
        if not generated_at:
            continue
        records.append(
            {
                "project_id": PROJECT_ID,
                "generated_at": generated_at,
                "data": item.get("data", {}),
                "report_text": item.get("report_text", ""),
                "source": table_name,
            }
        )
    records.sort(key=lambda item: item["generated_at"])
    return records


def load_history(table_name: str = "") -> List[Dict[str, Any]]:
    records = _load_dynamodb_history(table_name)
    if records:
        return records
    return []


def _filter_history_as_of(history: List[Dict[str, Any]], as_of: Optional[dt.datetime]) -> List[Dict[str, Any]]:
    if as_of is None:
        return history
    if as_of.tzinfo is None:
        as_of = as_of.replace(tzinfo=REPORT_TIMEZONE)
    return [record for record in history if record["generated_at"] <= as_of]


def _market_value(data: Dict[str, Any], region: str, label: str) -> Optional[float]:
    rows = data.get("markets", {}).get(region, [])
    for row in rows:
        if row.get("label") == label:
            value = row.get("price")
            try:
                return float(value)
            except (TypeError, ValueError):
                return None
    return None


def _macro_value(data: Dict[str, Any], series_id: str) -> Optional[float]:
    row = data.get("macro", {}).get(series_id, {})
    try:
        value = row.get("value")
        return float(value) if value is not None else None
    except (TypeError, ValueError):
        return None


def _strategy_exposure(verdict: str) -> float:
    if verdict == "위험 우위":
        return 0.0
    if verdict == "주의":
        return 0.5
    return 1.0


def _regime_label(data: Dict[str, Any]) -> str:
    dashboard = _risk_dashboard(data)
    score = float(dashboard.get("score", 0) or 0)
    us_vix = _market_value(data, "US", "VIX")
    spy_1m = None
    for row in data.get("trend", {}).get("items", {}).get("1mo", []):
        if row.get("label") == "S&P 500 ETF":
            try:
                spy_1m = float(row.get("change_pct"))
            except (TypeError, ValueError):
                spy_1m = None
            break
    if isinstance(us_vix, (int, float)) and us_vix >= 25:
        return "고변동성/방어"
    if score >= 5:
        return "방어 우위"
    if isinstance(spy_1m, (int, float)) and spy_1m >= 3 and score <= 1:
        return "상승 우호"
    if score <= 1:
        return "횡보/중립"
    return "주의/혼조"


def _round_trip_cost(exposure: float) -> float:
    if exposure <= 0:
        return 0.0
    return exposure * (ROUND_TRIP_COST_PCT + SLIPPAGE_PCT)


def _baseline_returns(asset_return: float) -> Dict[str, float]:
    return {
        "always_invest_return_pct": asset_return,
        "always_cash_return_pct": 0.0,
    }


def _decision_trade_settings(decision_snapshot: Dict[str, Any], predicted_verdict: str) -> Tuple[str, float]:
    candidates: List[Dict[str, Any]] = []
    if isinstance(decision_snapshot, dict):
        candidates.append(decision_snapshot)
        engine = decision_snapshot.get("engine")
        if isinstance(engine, dict):
            candidates.append(engine)
        ai_signal = decision_snapshot.get("ai_signal")
        if isinstance(ai_signal, dict):
            candidates.append(ai_signal)

    for section in candidates:
        trade_mode = str(section.get("trade_mode", "")).strip()
        position_size = section.get("position_size")
        if trade_mode or position_size is not None:
            try:
                return trade_mode, float(position_size if position_size is not None else 0.0)
            except (TypeError, ValueError):
                return trade_mode, 0.0

    if predicted_verdict == "위험 우위":
        return "현금 우선", 0.0
    if predicted_verdict == "주의":
        return "조건부", 50.0
    return "실전 후보", 100.0


def _krw_value(data: Dict[str, Any], region: str, label: str) -> Optional[float]:
    value = _market_value(data, region, label)
    if value is None:
        return None
    if region == "US":
        fx = _market_value(data, "Korea", "USD/KRW")
        if fx in (None, 0):
            fx = 1.0
        return value * fx
    return value


def _return_pct_from_values(base_value: Optional[float], future_value: Optional[float]) -> Optional[float]:
    if base_value in (None, 0) or future_value is None:
        return None
    return (future_value / base_value - 1) * 100


def _future_24h_match(history: List[Dict[str, Any]], idx: int) -> Optional[Tuple[Dict[str, Any], str, float]]:
    generated_at = history[idx]["generated_at"]
    target = generated_at + dt.timedelta(hours=TARGET_FUTURE_GAP_HOURS)
    tolerance = dt.timedelta(hours=FUTURE_GAP_TOLERANCE_HOURS)
    best_candidate: Optional[Dict[str, Any]] = None
    best_delta: Optional[dt.timedelta] = None

    for candidate in history[idx + 1 :]:
        gap = candidate["generated_at"] - generated_at
        if gap < dt.timedelta(0):
            continue
        if gap > dt.timedelta(hours=TARGET_FUTURE_GAP_HOURS + FUTURE_GAP_TOLERANCE_HOURS):
            break
        delta = abs(gap - dt.timedelta(hours=TARGET_FUTURE_GAP_HOURS))
        if delta > tolerance:
            continue
        if best_delta is None or delta < best_delta:
            best_candidate = candidate
            best_delta = delta

    if best_candidate is None:
        return None
    return best_candidate, "24h", 1.0


def _portfolio_returns(rows: List[Dict[str, Any]], left_key: str, right_key: str, left_weight: float) -> List[float]:
    right_weight = 1.0 - left_weight
    returns: List[float] = []
    for row in rows:
        left = row.get(left_key)
        right = row.get(right_key)
        if left is None or right is None:
            continue
        returns.append(left_weight * float(left) + right_weight * float(right))
    return returns


def _best_two_asset_mix(rows: List[Dict[str, Any]], left_key: str, right_key: str) -> Dict[str, Any]:
    usable = [row for row in rows if row.get(left_key) is not None and row.get(right_key) is not None]
    if not usable:
        return {
            "left_weight": 0.5,
            "right_weight": 0.5,
            "method": "균등 비중",
            "compound_return_pct": 0.0,
            "max_drawdown_pct": 0.0,
            "sample_count": 0,
        }

    best = {
        "left_weight": 0.5,
        "right_weight": 0.5,
        "method": "균등 비중",
        "compound_return_pct": -10**9,
        "max_drawdown_pct": 0.0,
        "sample_count": len(usable),
    }
    for step_index in range(21):
        left_weight = step_index / 20.0
        blended_returns = _portfolio_returns(usable, left_key, right_key, left_weight)
        if not blended_returns:
            continue
        capital = [100.0]
        for ret in blended_returns:
            capital.append(capital[-1] + ret)
        compound_return_pct = capital[-1] - 100.0
        max_drawdown_pct = _max_drawdown(capital)
        score = compound_return_pct + (max_drawdown_pct * 0.25)
        if score > best["compound_return_pct"] + (best["max_drawdown_pct"] * 0.25):
            best = {
                "left_weight": left_weight,
                "right_weight": 1.0 - left_weight,
                "method": "KRW 누적수익률 그리드 탐색",
                "compound_return_pct": compound_return_pct,
                "max_drawdown_pct": max_drawdown_pct,
                "sample_count": len(usable),
            }
    return best


def _weighted_mean(values: List[float], weights: List[float]) -> float:
    if not values or not weights:
        return 0.0
    total_weight = sum(weights)
    if total_weight <= 0:
        return 0.0
    return sum(value * weight for value, weight in zip(values, weights)) / total_weight


def _weighted_linear_fit(points: List[Tuple[float, float]], weights: List[float]) -> Dict[str, float]:
    if not points:
        return {"slope": 0.0, "intercept": 0.0, "r2": 0.0}
    if len(points) == 1:
        _, y = points[0]
        return {"slope": 0.0, "intercept": y, "r2": 0.0}
    total_weight = sum(weights)
    if total_weight <= 0:
        return _linear_fit(points)
    mean_x = sum(x * w for (x, _), w in zip(points, weights)) / total_weight
    mean_y = sum(y * w for (_, y), w in zip(points, weights)) / total_weight
    denom = sum(w * (x - mean_x) ** 2 for (x, _), w in zip(points, weights))
    if denom == 0:
        return {"slope": 0.0, "intercept": mean_y, "r2": 0.0}
    slope = sum(w * (x - mean_x) * (y - mean_y) for (x, y), w in zip(points, weights)) / denom
    intercept = mean_y - slope * mean_x
    ss_tot = sum(w * (y - mean_y) ** 2 for (_, y), w in zip(points, weights))
    ss_res = sum(w * (y - (slope * x + intercept)) ** 2 for (x, y), w in zip(points, weights))
    r2 = 1 - (ss_res / ss_tot) if ss_tot else 0.0
    return {"slope": slope, "intercept": intercept, "r2": max(0.0, min(1.0, r2))}


def _proxy_return(base: Dict[str, Any], future: Dict[str, Any]) -> Optional[float]:
    base_value = _market_value(base, "US", "S&P 500")
    future_value = _market_value(future, "US", "S&P 500")
    if base_value in (None, 0) or future_value is None:
        return None
    return (future_value - base_value) / base_value * 100


def _proxy_return_by_label(base: Dict[str, Any], future: Dict[str, Any], region: str, label: str) -> Optional[float]:
    base_value = _market_value(base, region, label)
    future_value = _market_value(future, region, label)
    if base_value in (None, 0) or future_value is None:
        return None
    return (future_value - base_value) / base_value * 100


def _actual_market_score(base: Dict[str, Any], future: Dict[str, Any]) -> Dict[str, Any]:
    score = 0.0
    components: List[Dict[str, Any]] = []

    def add_change(name: str, base_value: Optional[float], future_value: Optional[float], bullish: bool = True, weight: float = 1.0) -> None:
        nonlocal score
        if base_value in (None, 0) or future_value is None:
            return
        change_pct = (future_value - base_value) / base_value * 100
        if bullish:
            delta = -weight if change_pct > 0 else weight if change_pct < 0 else 0
        else:
            delta = weight if change_pct > 0 else -weight if change_pct < 0 else 0
        score += delta
        components.append({"name": name, "change_pct": change_pct, "score_delta": delta})

    add_change("SPY", _market_value(base, "US", "S&P 500"), _market_value(future, "US", "S&P 500"), bullish=True, weight=1.5)
    add_change("Nasdaq", _market_value(base, "US", "Nasdaq"), _market_value(future, "US", "Nasdaq"), bullish=True, weight=1.0)
    add_change("RSP", _market_value(base, "US", "Equal Weight S&P 500"), _market_value(future, "US", "Equal Weight S&P 500"), bullish=True, weight=1.0)
    add_change("IWM", _market_value(base, "US", "Russell 2000"), _market_value(future, "US", "Russell 2000"), bullish=True, weight=1.0)
    add_change("HYG", _market_value(base, "US", "High Yield Bond ETF"), _market_value(future, "US", "High Yield Bond ETF"), bullish=True, weight=1.0)
    add_change("LQD", _market_value(base, "US", "Investment Grade Bond ETF"), _market_value(future, "US", "Investment Grade Bond ETF"), bullish=True, weight=0.5)
    add_change("VIX", _market_value(base, "US", "VIX"), _market_value(future, "US", "VIX"), bullish=False, weight=2.0)
    add_change("US10Y", _market_value(base, "US", "US 10Y Yield"), _market_value(future, "US", "US 10Y Yield"), bullish=False, weight=1.0)
    add_change("Dollar", _market_value(base, "US", "Dollar Index"), _market_value(future, "US", "Dollar Index"), bullish=False, weight=1.0)
    add_change("WTI", _market_value(base, "US", "WTI Oil"), _market_value(future, "US", "WTI Oil"), bullish=False, weight=0.5)
    add_change("SMH", _market_value(base, "US", "Semiconductors ETF"), _market_value(future, "US", "Semiconductors ETF"), bullish=True, weight=1.0)

    if score >= 3:
        verdict = "위험 우위"
    elif score >= 1:
        verdict = "주의"
    else:
        verdict = "중립~우호"
    return {"score": score, "verdict": verdict, "components": components}


def _verdict_hit(predicted: str, actual: str) -> bool:
    return predicted == actual


def _format_pct(value: float) -> str:
    sign = "+" if value > 0 else ""
    return f"{sign}{value:.2f}%"


def _summary_lines(rows: List[Dict[str, Any]]) -> List[str]:
    if not rows:
        return ["- 평가 가능한 리포트가 아직 충분하지 않습니다."]
    total_weight = sum(float(row.get("weight", 1.0) or 1.0) for row in rows)
    verdict_hits = sum(float(row.get("weight", 1.0) or 1.0) for row in rows if row["verdict_hit"])
    avg_error = _weighted_mean(
        [abs(row["predicted_score"] - row["actual_score"]) for row in rows],
        [float(row.get("weight", 1.0) or 1.0) for row in rows],
    )
    component_accuracy = sum(
        float(row.get("component_hits", 0)) * float(row.get("weight", 1.0) or 1.0) for row in rows
    ) / max(
        sum(float(row.get("component_total", 0)) * float(row.get("weight", 1.0) or 1.0) for row in rows),
        1.0,
    )
    lines = [
        f"- 평가 개수: {len(rows)}",
        f"- 가중 샘플: {total_weight:.1f}",
        f"- 판단 적중률: {verdict_hits:.1f}/{total_weight:.1f} ({(verdict_hits / total_weight * 100 if total_weight else 0.0):.1f}%)",
        f"- 평균 점수 오차: {avg_error:.2f}",
        f"- 구성요소 방향 적중률: {component_accuracy * 100:.1f}%",
        f"- 비교 간격: 24시간 고정(±{FUTURE_GAP_TOLERANCE_HOURS}시간 허용)",
    ]
    misses = []
    seen = set()
    for row in rows:
        if row["verdict_hit"]:
            continue
        key = row["generated_at"]
        if key in seen:
            continue
        seen.add(key)
        misses.append(row)
        if len(misses) >= 3:
            break
    if misses:
        lines.append("- 주요 미스:")
        for row in misses:
            lines.append(
                f"  - {row['generated_at']} predicted {row['predicted_verdict']} / actual {row['actual_verdict']}"
                f" (error {row['predicted_score'] - row['actual_score']:+.2f})"
            )
    return lines


def _compound_return(returns: List[float]) -> float:
    value = 1.0
    for ret in returns:
        value *= 1 + ret / 100.0
    return (value - 1) * 100


def _max_drawdown(capital_series: List[float]) -> float:
    if not capital_series:
        return 0.0
    peak = capital_series[0]
    worst = 0.0
    for capital in capital_series:
        if capital > peak:
            peak = capital
        if peak > 0:
            drawdown = (capital - peak) / peak * 100
            if drawdown < worst:
                worst = drawdown
    return worst


def _compute_rows(history: List[Dict[str, Any]], window_days: int = 7, as_of: Optional[dt.datetime] = None) -> List[Dict[str, Any]]:
    anchor = as_of or dt.datetime.now(REPORT_TIMEZONE)
    if anchor.tzinfo is None:
        anchor = anchor.replace(tzinfo=REPORT_TIMEZONE)
    cutoff = anchor - dt.timedelta(days=window_days)
    history = _filter_history_as_of(history, as_of)
    rows: List[Dict[str, Any]] = []
    for idx, base in enumerate(history):
        generated_at = base["generated_at"]
        if generated_at < cutoff:
            continue
        predicted = _risk_dashboard(base["data"])
        regime = _regime_label(base["data"])
        match = _future_24h_match(history, idx)
        if not match:
            continue
        future, horizon_label, weight = match
        actual = _actual_market_score(base["data"], future["data"])
        predicted_verdict = predicted["verdict"]
        actual_verdict = actual["verdict"]
        components = actual["components"]
        component_hits = 0
        component_total = 0
        for comp in components:
            component_total += 1
            if comp["score_delta"] == 0:
                continue
            if comp["score_delta"] < 0 and comp["change_pct"] > 0:
                component_hits += 1
            elif comp["score_delta"] > 0 and comp["change_pct"] < 0:
                component_hits += 1
        rows.append(
            {
                "generated_at": generated_at.isoformat(),
                "predicted_verdict": predicted_verdict,
                "actual_verdict": actual_verdict,
                "predicted_score": predicted["score"],
                "actual_score": actual["score"],
                "verdict_hit": _verdict_hit(predicted_verdict, actual_verdict),
                "component_hits": component_hits,
                "component_total": component_total,
                "regime": regime,
                "horizon": horizon_label,
                "weight": weight,
                "base_source": base["source"],
                "future_source": future["source"],
            }
        )
    return rows


def _compute_return_rows(history: List[Dict[str, Any]], window_days: int = 7, as_of: Optional[dt.datetime] = None) -> List[Dict[str, Any]]:
    anchor = as_of or dt.datetime.now(REPORT_TIMEZONE)
    if anchor.tzinfo is None:
        anchor = anchor.replace(tzinfo=REPORT_TIMEZONE)
    cutoff = anchor - dt.timedelta(days=window_days)
    history = _filter_history_as_of(history, as_of)
    raw_rows: List[Dict[str, Any]] = []
    current_exposure = 0.0
    for idx, base in enumerate(history):
        generated_at = base["generated_at"]
        if generated_at < cutoff:
            continue
        predicted = _risk_dashboard(base["data"])
        match = _future_24h_match(history, idx)
        if not match:
            continue
        future, horizon_label, weight = match
        spy_base = _krw_value(base["data"], "US", "S&P 500")
        spy_future = _krw_value(future["data"], "US", "S&P 500")
        kospi_base = _krw_value(base["data"], "Korea", "KOSPI")
        kospi_future = _krw_value(future["data"], "Korea", "KOSPI")
        spy_return = _return_pct_from_values(spy_base, spy_future)
        kospi_return = _return_pct_from_values(kospi_base, kospi_future)
        if spy_return is None and kospi_return is None:
            continue
        decision_snapshot = base.get("decision_snapshot") or {}
        trade_mode, position_size = _decision_trade_settings(decision_snapshot, predicted.get("verdict", "중립~우호"))
        if trade_mode == "현금 우선":
            action = "판매"
            target_exposure = 0.0
        elif trade_mode == "관망":
            action = "관망"
            target_exposure = current_exposure
        else:
            action = "구매"
            target_exposure = max(0.0, min(1.0, position_size / 100.0))
        turnover = abs(target_exposure - current_exposure)
        trade_cost_pct = turnover * POSITION_CHANGE_COST_PCT
        raw_rows.append(
            {
                "generated_at": generated_at.isoformat(),
                "future_generated_at": future["generated_at"].isoformat(),
                "predicted_verdict": predicted["verdict"],
                "predicted_score": predicted["score"],
                "previous_exposure": current_exposure,
                "exposure": target_exposure,
                "action": action,
                "spy_return_pct": spy_return,
                "kospi_return_pct": kospi_return,
                "trade_cost_pct": trade_cost_pct,
                "turnover_pct": turnover,
                "weight": weight,
                "horizon": horizon_label,
                "regime": _regime_label(base["data"]),
                "base_source": base["source"],
                "future_source": future["source"],
            }
        )
    basket_mix = MARKET_BENCHMARK_WEIGHTS
    rows: List[Dict[str, Any]] = []
    for row in raw_rows:
        spy_return = row.get("spy_return_pct")
        kospi_return = row.get("kospi_return_pct")
        if spy_return is not None and kospi_return is not None:
            benchmark_return = 0.5 * float(spy_return) + 0.5 * float(kospi_return)
        else:
            benchmark_return = float(spy_return if spy_return is not None else kospi_return)
        strategy_return = (benchmark_return * float(row["exposure"])) - float(row["trade_cost_pct"])
        baselines = _baseline_returns(benchmark_return)
        rows.append(
            {
                **row,
                "benchmark_return_pct": benchmark_return,
                "strategy_return_pct": strategy_return,
                "benchmark_weights": basket_mix,
                "asset_name": "SPY/KOSPI KRW basket",
                "decision": row["action"],
                "round_trip_cost_pct": row["trade_cost_pct"],
                **baselines,
            }
        )
        current_exposure = target_exposure
    return rows


def _linear_fit(points: List[Tuple[float, float]]) -> Dict[str, float]:
    if len(points) < 2:
        x, y = points[0] if points else (0.0, 0.0)
        return {"slope": 0.0, "intercept": y, "r2": 0.0}
    xs = [x for x, _ in points]
    ys = [y for _, y in points]
    mean_x = sum(xs) / len(xs)
    mean_y = sum(ys) / len(ys)
    denom = sum((x - mean_x) ** 2 for x in xs)
    slope = sum((x - mean_x) * (y - mean_y) for x, y in points) / denom if denom else 0.0
    intercept = mean_y - slope * mean_x
    ss_tot = sum((y - mean_y) ** 2 for y in ys)
    ss_res = sum((y - (slope * x + intercept)) ** 2 for x, y in points)
    r2 = 1 - (ss_res / ss_tot) if ss_tot else 0.0
    return {"slope": slope, "intercept": intercept, "r2": max(0.0, min(1.0, r2))}


def _mean(values: List[float]) -> float:
    return sum(values) / len(values) if values else 0.0


def _median(values: List[float]) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    mid = len(ordered) // 2
    if len(ordered) % 2:
        return ordered[mid]
    return (ordered[mid - 1] + ordered[mid]) / 2


def build_calibration_profile(table_name: str = "", window_days: int = 30, as_of: Optional[dt.datetime] = None) -> Dict[str, Any]:
    history = load_history(table_name=table_name)
    return_rows = _compute_return_rows(history, window_days=window_days, as_of=as_of)
    if not return_rows:
        return {
            "sample_count": 0,
            "raw_sample_count": 0,
            "effective_sample_count": 0.0,
            "walk_forward_mae": None,
            "walk_forward_direction_hit_rate": None,
            "model": {"slope": 0.0, "intercept": 0.0, "r2": 0.0},
            "verdict_stats": {},
            "score_stats": {},
            "expected_return_pct": 0.0,
            "recommended_position_multiplier": 0.0,
            "benchmark_weights": {"left_weight": 0.5, "right_weight": 0.5, "method": "균등 비중"},
        }

    wf_errors: List[float] = []
    wf_error_weights: List[float] = []
    wf_hits: List[int] = []
    wf_hit_weights: List[float] = []
    train_min = 3 if len(return_rows) >= 3 else 1
    for idx, row in enumerate(return_rows):
        if idx < train_min:
            continue
        train_points = [(prior["predicted_score"], prior["strategy_return_pct"]) for prior in return_rows[:idx]]
        train_weights = [float(prior.get("weight", 1.0) or 1.0) for prior in return_rows[:idx]]
        model = _weighted_linear_fit(train_points, train_weights)
        predicted_return = model["slope"] * row["predicted_score"] + model["intercept"]
        row_weight = float(row.get("weight", 1.0) or 1.0)
        wf_errors.append(abs(predicted_return - row["strategy_return_pct"]))
        wf_error_weights.append(row_weight)
        wf_hits.append(int((predicted_return >= 0) == (row["strategy_return_pct"] >= 0)))
        wf_hit_weights.append(row_weight)

    final_points = [(row["predicted_score"], row["strategy_return_pct"]) for row in return_rows]
    final_weights = [float(row.get("weight", 1.0) or 1.0) for row in return_rows]
    final_model = _weighted_linear_fit(final_points, final_weights)
    verdict_stats: Dict[str, Dict[str, Any]] = {}
    score_bins: Dict[int, List[float]] = {}
    for row in return_rows:
        verdict = row["predicted_verdict"]
        verdict_bucket = verdict_stats.setdefault(verdict, {"count": 0, "returns": [], "exposure": []})
        verdict_bucket["count"] += 1
        verdict_bucket["returns"].append(row["strategy_return_pct"])
        verdict_bucket["exposure"].append(row["exposure"])
        bucket = int(round(row["predicted_score"]))
        score_bins.setdefault(bucket, []).append(row["strategy_return_pct"])
    score_stats = {
        str(bucket): {
            "count": len(values),
            "avg_return_pct": _mean(values),
            "median_return_pct": _median(values),
        }
        for bucket, values in sorted(score_bins.items())
    }
    verdict_stats_final = {
        verdict: {
            "count": payload["count"],
            "avg_return_pct": _mean(payload["returns"]),
            "median_return_pct": _median(payload["returns"]),
            "avg_exposure": _mean(payload["exposure"]),
        }
        for verdict, payload in verdict_stats.items()
    }
    latest_row = return_rows[-1]
    expected_return_pct = final_model["slope"] * latest_row["predicted_score"] + final_model["intercept"]
    recommended_position_multiplier = max(
        0.0,
        min(1.25, 0.55 + (max(expected_return_pct, 0.0) / 6.0) + (0.2 if final_model["r2"] >= 0.15 else 0.0)),
    )
    weighted_sample_count = sum(final_weights)
    benchmark_weights = latest_row.get("benchmark_weights") or MARKET_BENCHMARK_WEIGHTS
    return {
        "sample_count": int(round(weighted_sample_count)),
        "raw_sample_count": len(return_rows),
        "effective_sample_count": weighted_sample_count,
        "walk_forward_mae": _weighted_mean(wf_errors, wf_error_weights) if wf_errors else None,
        "walk_forward_direction_hit_rate": (_weighted_mean(wf_hits, wf_hit_weights) if wf_hits else None),
        "model": final_model,
        "verdict_stats": verdict_stats_final,
        "score_stats": score_stats,
        "expected_return_pct": expected_return_pct,
        "recommended_position_multiplier": recommended_position_multiplier,
        "avg_abs_return_pct": _mean([abs(row["strategy_return_pct"]) for row in return_rows]),
        "last_generated_at": latest_row["future_generated_at"],
        "benchmark_weights": benchmark_weights,
        "benchmark_return_pct": latest_row.get("benchmark_return_pct"),
    }


def build_reliability_guard(calibration: Dict[str, Any], context: str = "market") -> Dict[str, Any]:
    sample_count = int(calibration.get("sample_count", 0) or 0)
    mae = calibration.get("walk_forward_mae")
    hit_rate = calibration.get("walk_forward_direction_hit_rate")

    if context == "crypto":
        min_samples = 5
        mae_warn = 3.2
        mae_fail = 4.5
        hit_warn = 0.50
        hit_fail = 0.42
        cap_warn = 0.80
        cap_fail = 0.50
    else:
        min_samples = 5
        mae_warn = 2.2
        mae_fail = 3.0
        hit_warn = 0.52
        hit_fail = 0.45
        cap_warn = 0.85
        cap_fail = 0.55

    if sample_count < min_samples or mae is None or hit_rate is None:
        return {
            "state": "미검증",
            "sample_count": sample_count,
            "confidence_delta": 0,
            "position_multiplier_cap": 1.0,
            "max_trade_mode": "실전 후보",
            "note": "표본이 아직 충분하지 않아 자동 보정은 대기합니다.",
        }

    if mae >= mae_fail or hit_rate <= hit_fail:
        return {
            "state": "불안정",
            "sample_count": sample_count,
            "confidence_delta": -8,
            "position_multiplier_cap": cap_fail,
            "max_trade_mode": "관망",
            "note": "walk-forward 성과가 약해 보수적으로 차단합니다.",
        }
    if mae >= mae_warn or hit_rate <= hit_warn:
        return {
            "state": "주의",
            "sample_count": sample_count,
            "confidence_delta": -4,
            "position_multiplier_cap": cap_warn,
            "max_trade_mode": "조건부",
            "note": "walk-forward 성과가 애매해 비중을 줄입니다.",
        }
    return {
        "state": "안정",
        "sample_count": sample_count,
        "confidence_delta": 3,
        "position_multiplier_cap": 1.10,
        "max_trade_mode": "실전 후보",
        "note": "walk-forward 성과가 양호해 자동 보정을 완화합니다.",
    }


def _return_period(rows: List[Dict[str, Any]]) -> Optional[Tuple[str, str]]:
    if not rows:
        return None
    return rows[0]["generated_at"], rows[-1].get("future_generated_at", rows[-1]["generated_at"])


def _return_summary_lines(rows: List[Dict[str, Any]], calibration: Optional[Dict[str, Any]] = None) -> List[str]:
    if not rows:
        return ["- 평가 가능한 수익률 샘플이 아직 충분하지 않습니다."]
    period = _return_period(rows)
    total_weight = sum(float(row.get("weight", 1.0) or 1.0) for row in rows)
    strategy_capital = [100.0]
    spy_capital = [100.0]
    kospi_capital = [100.0]
    baseline_invest_capital = [100.0]
    baseline_cash_capital = [100.0]
    kospi_rows = 0
    for row in rows:
        strategy_capital.append(strategy_capital[-1] + row["strategy_return_pct"])
        spy_capital.append(spy_capital[-1] + row["spy_return_pct"])
        baseline_invest_capital.append(baseline_invest_capital[-1] + row["always_invest_return_pct"])
        baseline_cash_capital.append(baseline_cash_capital[-1] + row["always_cash_return_pct"])
        if row.get("kospi_return_pct") is not None:
            kospi_capital.append(kospi_capital[-1] + row["kospi_return_pct"])
            kospi_rows += 1
        else:
            kospi_capital.append(kospi_capital[-1])
    strategy_total = strategy_capital[-1] - 100.0
    spy_total = spy_capital[-1] - 100.0
    kospi_total = kospi_capital[-1] - 100.0 if kospi_rows else None
    baseline_invest_total = baseline_invest_capital[-1] - 100.0
    baseline_cash_total = baseline_cash_capital[-1] - 100.0
    win_rate = sum(float(row.get("weight", 1.0) or 1.0) for row in rows if row["strategy_return_pct"] > 0) / max(total_weight, 1.0) * 100
    spy_outperformance = strategy_total - spy_total
    kospi_outperformance = (strategy_total - kospi_total) if kospi_total is not None else None
    bench = calibration.get("benchmark_weights") if calibration else None
    lines = [
        f"- 샘플 개수: {len(rows)}",
        f"- 가중 샘플: {total_weight:.1f}",
        f"- 평가 기간: {period[0]} ~ {period[1]}" if period else "- 평가 기간: -",
        f"- 전략 단순합 수익률: {_format_pct(strategy_total)}",
        f"- S&P 500(원화) 단순합 수익률: {_format_pct(spy_total)}",
        f"- S&P 500 초과 수익률: {_format_pct(spy_outperformance)}",
        f"- KOSPI 단순합 수익률: {_format_pct(kospi_total)}" if kospi_total is not None else "- KOSPI 단순합 수익률: 계산 불가",
        f"- KOSPI 초과 수익률: {_format_pct(kospi_outperformance)}" if kospi_outperformance is not None else "- KOSPI 초과 수익률: 계산 불가",
        f"- 항상 투자 기준: {_format_pct(baseline_invest_total)}",
        f"- 항상 현금 기준: {_format_pct(baseline_cash_total)}",
        f"- 전략 승률: {win_rate:.1f}%",
        f"- 최대 낙폭: {_format_pct(_max_drawdown(strategy_capital))}",
        f"- 투자 비중 규칙: 위험 우위=판매, 관망=이전 포지션 유지, 그 외는 권장 비중으로 구매",
        f"- 대표 자산: S&P 500과 KOSPI를 원화 기준으로 합성",
    ]
    if bench:
        lines.insert(
            5,
            (
                f"- KRW 바스켓 비중: S&P 500 {bench.get('left_weight', 0.5) * 100:.0f}% / "
                f"KOSPI {bench.get('right_weight', 0.5) * 100:.0f}%"
            ),
        )
        if bench.get("method"):
            lines.insert(6, f"- 비중 산출 방식: {bench['method']}")
    if rows:
        preview = rows[:3]
        lines.append("- 주요 샘플:")
        for row in preview:
            kospi_text = _format_pct(row["kospi_return_pct"]) if row.get("kospi_return_pct") is not None else "N/A"
            basket_text = _format_pct(row["benchmark_return_pct"]) if row.get("benchmark_return_pct") is not None else "N/A"
            lines.append(
                f"  - {row['generated_at']} [{row.get('regime', 'n/a')}] verdict {row['predicted_verdict']} / "
                f"{row['decision']} / 전략 {_format_pct(row['strategy_return_pct'])} "
                f"(cost {_format_pct(row.get('round_trip_cost_pct', 0.0))}) / "
                f"KRW 바스켓 {basket_text} / S&P 500 {_format_pct(row['spy_return_pct'])} / KOSPI {kospi_text}"
            )
    return lines


def _regime_summary_lines(rows: List[Dict[str, Any]]) -> List[str]:
    if not rows:
        return ["- 국면별 성능: 샘플이 부족합니다."]
    buckets: Dict[str, List[float]] = {}
    verdict_hits: Dict[str, List[int]] = {}
    weights: Dict[str, List[float]] = {}
    for row in rows:
        regime = row.get("regime", "n/a")
        value = row.get("strategy_return_pct")
        if value is None:
            continue
        buckets.setdefault(regime, []).append(value)
        verdict_hits.setdefault(regime, []).append(int(bool(row.get("verdict_hit"))))
        weights.setdefault(regime, []).append(float(row.get("weight", 1.0) or 1.0))
    lines = ["- 국면별 성능:"]
    for regime in sorted(buckets):
        vals = buckets[regime]
        hits = verdict_hits.get(regime, [])
        weights_for_regime = weights.get(regime, [])
        hit_text = ""
        if hits:
            hit_text = f", 판단 적중률 {_weighted_mean([float(hit) for hit in hits], weights_for_regime) * 100:.1f}%"
        lines.append(
            f"  - {regime}: 샘플 {len(vals)}, 가중 {sum(weights_for_regime):.1f}, 평균 수익률 {_format_pct(_weighted_mean(vals, weights_for_regime))}{hit_text}"
        )
    return lines


def build_weekly_performance_report(table_name: str = "", window_days: int = 14, as_of: Optional[dt.datetime] = None) -> Dict[str, Any]:
    history = load_history(table_name=table_name)
    rows = _compute_rows(history, window_days=window_days, as_of=as_of)
    return_rows = _compute_return_rows(history, window_days=window_days, as_of=as_of)
    calibration = build_calibration_profile(table_name=table_name, window_days=max(window_days, 30), as_of=as_of)
    reliability = build_reliability_guard(calibration, context="market")
    report_time = as_of or dt.datetime.now(REPORT_TIMEZONE)
    if report_time.tzinfo is None:
        report_time = report_time.replace(tzinfo=REPORT_TIMEZONE)
    today = report_time.strftime("%Y-%m-%d")
    lines = [
        f"[{today} 사후 검증 리포트 - Market Agent]",
        "",
        *_summary_lines(rows),
        "",
        "[수익률 백테스트]",
        *(_return_summary_lines(return_rows, calibration=calibration)),
        "",
        "[walk-forward 보정]",
        f"- 샘플 개수: {calibration['sample_count']} (원시 {calibration.get('raw_sample_count', 0)}, 가중 {calibration.get('effective_sample_count', 0.0):.1f})",
        f"- 회귀모형: return = {calibration['model']['slope']:+.4f} * score + {calibration['model']['intercept']:+.4f}",
        f"- walk-forward MAE: {calibration['walk_forward_mae']:.2f}%" if calibration["walk_forward_mae"] is not None else "- walk-forward MAE: n/a",
        f"- 방향 적중률: {calibration['walk_forward_direction_hit_rate'] * 100:.1f}%" if calibration["walk_forward_direction_hit_rate"] is not None else "- 방향 적중률: n/a",
        f"- 현재 점수 기준 기대수익: {calibration['expected_return_pct']:+.2f}%",
        f"- KRW 바스켓 비중: S&P 500 {calibration.get('benchmark_weights', {}).get('left_weight', 0.5) * 100:.0f}% / KOSPI {calibration.get('benchmark_weights', {}).get('right_weight', 0.5) * 100:.0f}%",
        "",
        "[자동 개선 판정]",
        f"- 상태: {reliability['state']}",
        f"- 보정 메시지: {reliability['note']}",
        f"- 샘플 개수: {reliability['sample_count']}",
        f"- 다음 리포트 최대 모드: {reliability['max_trade_mode']}",
        f"- 비중 상한: x{reliability['position_multiplier_cap']:.2f}",
        "",
        "[국면별 검증]",
        *(_regime_summary_lines(return_rows)),
        "",
        "[이 문서가 의미하는 것]",
        "- 이 문서는 두 부분으로 나뉩니다. 위쪽은 판단 검증, 아래쪽은 수익률 백테스트입니다.",
        f"- 비교 대상은 각 리포트 뒤 {TARGET_FUTURE_GAP_HOURS}시간 전후 {FUTURE_GAP_TOLERANCE_HOURS}시간 이내의 다음 리포트입니다.",
        "- 판단 적중은 예측 verdict와 실제 verdict가 같았는지로 봅니다.",
        "- 평균 점수 오차는 룰 점수 차이, 구성요소 방향 적중률은 세부 신호가 같은 방향이었는지 봅니다.",
        "- 수익률 백테스트는 리포트 시점의 구매/관망/판매 결정을 그대로 따라 24시간 뒤 손익을 단순 합산한 결과입니다.",
        "",
        "[읽는 법]",
        "- 적중률이 낮으면 예측 기준이 너무 공격적이거나 기준선이 시장보다 앞서 갔다는 뜻입니다.",
        "- 점수 오차가 크면, 같은 방향이라도 강도를 과하게 잡고 있다는 뜻입니다.",
        "- 수익률 백테스트가 좋지 않으면, 리포트가 맞아도 투자 행동으로는 별로라는 뜻일 수 있습니다.",
        "",
        "주의: 수익률 백테스트는 대표 자산(S&P 500, KOSPI) 기준의 KRW 환산 사후 검증입니다.",
    ]
    return {
        "generated_at": report_time.isoformat(timespec="seconds"),
        "project_id": PROJECT_ID,
        "window_days": window_days,
        "rows": rows,
        "return_rows": return_rows,
        "return_period": _return_period(return_rows),
        "calibration": calibration,
        "reliability_guard": reliability,
        "report_text": "\n".join(lines),
    }


def save_performance_report(payload: Dict[str, Any], table_name: str = "") -> Tuple[Path, Path]:
    PERFORMANCE_DIR.mkdir(parents=True, exist_ok=True)
    stamp = dt.datetime.now(REPORT_TIMEZONE).strftime("%Y-%m-%d_%H%M%S")
    json_path = PERFORMANCE_DIR / f"{stamp}.json"
    md_path = PERFORMANCE_DIR / f"{stamp}.md"
    json_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    md_path.write_text(payload["report_text"] + "\n", encoding="utf-8")
    if table_name:
        try:
            import boto3  # type: ignore
        except Exception:
            return md_path, json_path
        session = boto3.session.Session()
        table = session.resource("dynamodb").Table(table_name)
        table.put_item(
            Item={
                "project_id": PROJECT_ID,
                "record_key": f"EVAL#{payload['generated_at']}",
                "record_type": "evaluation",
                "generated_at": payload["generated_at"],
                "window_days": payload["window_days"],
                "data": payload,
                "report_text": payload["report_text"],
            }
        )
    return md_path, json_path


def save_performance_report_to_s3(payload: Dict[str, Any], bucket_name: str, prefix: str = "performance") -> Dict[str, str]:
    if not bucket_name:
        raise ValueError("bucket_name is required")
    try:
        import boto3  # type: ignore
    except Exception as exc:
        raise RuntimeError("boto3 is required to save performance reports to S3") from exc

    session = boto3.session.Session()
    client = session.client("s3")
    generated_at = payload.get("generated_at") or dt.datetime.now(REPORT_TIMEZONE).isoformat(timespec="seconds")
    safe_stamp = generated_at.replace(":", "").replace("+", "_").replace("T", "_")
    base_key = f"{prefix.rstrip('/')}/{safe_stamp}"
    json_key = f"{base_key}.json"
    md_key = f"{base_key}.md"
    client.put_object(
        Bucket=bucket_name,
        Key=json_key,
        Body=(json.dumps(payload, ensure_ascii=False, indent=2) + "\n").encode("utf-8"),
        ContentType="application/json; charset=utf-8",
    )
    client.put_object(
        Bucket=bucket_name,
        Key=md_key,
        Body=(payload["report_text"] + "\n").encode("utf-8"),
        ContentType="text/markdown; charset=utf-8",
    )
    return {"bucket": bucket_name, "json_key": json_key, "md_key": md_key}
