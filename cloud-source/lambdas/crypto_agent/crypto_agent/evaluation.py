import datetime as dt
import json
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from .config import PERFORMANCE_DIR, REPORTS_DIR, REPORT_TIMEZONE
from .report import _risk_dashboard


PROJECT_ID = "crypto-agent"
MIN_FUTURE_GAP_HOURS = 18
MAX_FUTURE_GAP_HOURS = 30
STRATEGY_PROXY_LABEL = "Bitcoin"
ROUND_TRIP_COST_PCT = 0.35
SLIPPAGE_PCT = 0.20
BACKTEST_WINDOWS: Tuple[Tuple[int, int, float, str], ...] = (
    (18, 30, 1.0, "18-30h"),
    (42, 54, 0.7, "42-54h"),
    (66, 78, 0.5, "66-78h"),
)


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


def _load_local_history() -> List[Dict[str, Any]]:
    records = []
    for json_path in sorted(REPORTS_DIR.glob("*.json")):
        if json_path.name == "latest.json":
            continue
        try:
            data = json.loads(json_path.read_text(encoding="utf-8"))
        except Exception:
            continue
        generated_at = _parse_generated_at(data)
        if not generated_at:
            continue
        md_path = json_path.with_suffix(".md")
        report_text = md_path.read_text(encoding="utf-8") if md_path.exists() else ""
        records.append(
            {
                "project_id": PROJECT_ID,
                "generated_at": generated_at,
                "data": data,
                "report_text": report_text,
                "source": str(json_path),
            }
        )
    records.sort(key=lambda item: item["generated_at"])
    return records


def _load_dynamodb_history(table_name: str) -> List[Dict[str, Any]]:
    try:
        import boto3  # type: ignore
        from boto3.dynamodb.conditions import Key  # type: ignore
    except Exception:
        return []

    session = boto3.session.Session()
    table = session.resource("dynamodb").Table(table_name)
    records: List[Dict[str, Any]] = []
    response = table.query(KeyConditionExpression=Key("project_id").eq(PROJECT_ID))
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
    if table_name:
        records = _load_dynamodb_history(table_name)
        if records:
            return records
    return _load_local_history()


def _coin_value(data: Dict[str, Any], label: str) -> Optional[float]:
    for row in data.get("markets", {}).get("items", []):
        if row.get("label") == label:
            try:
                return float(row.get("price"))
            except (TypeError, ValueError):
                return None
    return None


def _coin_change_pct(data: Dict[str, Any], label: str, field: str = "change_pct_7d") -> Optional[float]:
    for row in data.get("markets", {}).get("items", []):
        if row.get("label") == label:
            try:
                return float(row.get(field))
            except (TypeError, ValueError):
                return None
    return None


def _market_context_value(data: Dict[str, Any], label: str) -> Optional[float]:
    items = data.get("market_context", {}).get("quotes", {}).get("items", {}).get("US", [])
    for row in items:
        if row.get("label") == label:
            try:
                return float(row.get("price"))
            except (TypeError, ValueError):
                return None
    return None


def _macro_value(data: Dict[str, Any], series_id: str) -> Optional[float]:
    row = data.get("market_context", {}).get("macro", {}).get("items", {}).get(series_id, {})
    try:
        value = row.get("value")
        return float(value) if value is not None else None
    except (TypeError, ValueError):
        return None


def _stablecoin_value(data: Dict[str, Any]) -> Optional[float]:
    try:
        return float(data.get("stablecoins", {}).get("total_circulating_usd"))
    except (TypeError, ValueError):
        return None


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


def _future_window_matches(history: List[Dict[str, Any]], idx: int) -> List[Tuple[Dict[str, Any], str, float]]:
    generated_at = history[idx]["generated_at"]
    matches: List[Tuple[Dict[str, Any], str, float]] = []
    for min_hours, max_hours, weight, label in BACKTEST_WINDOWS:
        future = None
        for candidate in history[idx + 1 :]:
            gap = candidate["generated_at"] - generated_at
            if gap < dt.timedelta(hours=min_hours):
                continue
            if gap > dt.timedelta(hours=max_hours):
                break
            future = candidate
            break
        if future:
            matches.append((future, label, weight))
    return matches


def _strategy_exposure(verdict: str) -> float:
    if verdict == "위험 우위":
        return 0.0
    if verdict == "주의":
        return 0.5
    return 1.0


def _regime_label(data: Dict[str, Any]) -> str:
    dashboard = _risk_dashboard(data)
    score = float(dashboard.get("score", 0) or 0)
    btc_7d = _coin_change_pct(data, "Bitcoin")
    stable_7d = data.get("stablecoins", {}).get("change_pct_7d")
    if score >= 5:
        return "고변동성/방어"
    if score >= 3:
        return "주의/혼조"
    if (
        isinstance(btc_7d, (int, float))
        and btc_7d >= 3
        and isinstance(stable_7d, (int, float))
        and stable_7d >= 0
        and score <= 1
    ):
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
        "always_invest_return_pct": asset_return - _round_trip_cost(1.0),
        "always_cash_return_pct": 0.0,
    }


def _proxy_return(base: Dict[str, Any], future: Dict[str, Any]) -> Optional[float]:
    base_value = _coin_value(base, STRATEGY_PROXY_LABEL)
    future_value = _coin_value(future, STRATEGY_PROXY_LABEL)
    if base_value in (None, 0) or future_value is None:
        return None
    return (future_value - base_value) / base_value * 100


def _actual_crypto_score(base: Dict[str, Any], future: Dict[str, Any]) -> Dict[str, Any]:
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

    add_change("BTC", _coin_value(base, "Bitcoin"), _coin_value(future, "Bitcoin"), bullish=True, weight=2.0)
    add_change("ETH", _coin_value(base, "Ethereum"), _coin_value(future, "Ethereum"), bullish=True, weight=1.5)
    add_change("SOL", _coin_value(base, "Solana"), _coin_value(future, "Solana"), bullish=True, weight=0.75)
    add_change("TOTAL_CAP", _coingecko_value(base, key="global.total_market_cap_usd"), _coingecko_value(future, key="global.total_market_cap_usd"), bullish=True, weight=1.5)
    add_change("BTC_DOM", _coingecko_value(base, key="global.btc_dominance_pct"), _coingecko_value(future, key="global.btc_dominance_pct"), bullish=False, weight=0.75)
    add_change("STABLE", _stablecoin_value(base), _stablecoin_value(future), bullish=True, weight=1.0)
    add_change("VIX", _market_context_value(base, "VIX"), _market_context_value(future, "VIX"), bullish=False, weight=1.25)
    add_change("DOLLAR", _market_context_value(base, "Dollar Index"), _market_context_value(future, "Dollar Index"), bullish=False, weight=1.0)
    add_change("HYG", _market_context_value(base, "High Yield Bond ETF"), _market_context_value(future, "High Yield Bond ETF"), bullish=True, weight=0.75)
    add_change("SPY", _market_context_value(base, "S&P 500"), _market_context_value(future, "S&P 500"), bullish=True, weight=0.75)
    add_change("TNX", _macro_value(base, "DGS10"), _macro_value(future, "DGS10"), bullish=False, weight=0.75)

    if score >= 3:
        verdict = "위험 우위"
    elif score >= 1:
        verdict = "주의"
    else:
        verdict = "중립~우호"
    return {"score": score, "verdict": verdict, "components": components}


def _coingecko_value(data: Dict[str, Any], key: str) -> Optional[float]:
    if key == "global.total_market_cap_usd":
        try:
            return float(data.get("global", {}).get("total_market_cap_usd"))
        except (TypeError, ValueError):
            return None
    if key == "global.btc_dominance_pct":
        try:
            return float(data.get("global", {}).get("btc_dominance_pct"))
        except (TypeError, ValueError):
            return None
    return None


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
        f"- 비교 간격: 18~30h, 42~54h, 66~78h를 가중 평균",
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


def _compute_rows(history: List[Dict[str, Any]], window_days: int = 7) -> List[Dict[str, Any]]:
    cutoff = dt.datetime.now(REPORT_TIMEZONE) - dt.timedelta(days=window_days)
    rows: List[Dict[str, Any]] = []
    for idx, base in enumerate(history):
        generated_at = base["generated_at"]
        if generated_at < cutoff:
            continue
        predicted = _risk_dashboard(base["data"])
        regime = _regime_label(base["data"])
        for future, horizon_label, weight in _future_window_matches(history, idx):
            actual = _actual_crypto_score(base["data"], future["data"])
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
                    "predicted_verdict": predicted["verdict"],
                    "actual_verdict": actual["verdict"],
                    "predicted_score": predicted["score"],
                    "actual_score": actual["score"],
                    "verdict_hit": predicted["verdict"] == actual["verdict"],
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


def _compute_return_rows(history: List[Dict[str, Any]], window_days: int = 7) -> List[Dict[str, Any]]:
    cutoff = dt.datetime.now(REPORT_TIMEZONE) - dt.timedelta(days=window_days)
    rows: List[Dict[str, Any]] = []
    for idx, base in enumerate(history):
        generated_at = base["generated_at"]
        if generated_at < cutoff:
            continue
        predicted = _risk_dashboard(base["data"])
        for future, horizon_label, weight in _future_window_matches(history, idx):
            asset_return = _proxy_return(base["data"], future["data"])
            if asset_return is None:
                continue
            exposure = _strategy_exposure(predicted["verdict"])
            round_trip_cost = _round_trip_cost(exposure)
            strategy_return = (asset_return * exposure) - round_trip_cost
            baselines = _baseline_returns(asset_return)
            benchmark_return = asset_return
            rows.append(
                {
                    "generated_at": generated_at.isoformat(),
                    "future_generated_at": future["generated_at"].isoformat(),
                    "predicted_verdict": predicted["verdict"],
                    "predicted_score": predicted["score"],
                    "exposure": exposure,
                    "asset_return_pct": asset_return,
                    "strategy_return_pct": strategy_return,
                    "benchmark_return_pct": benchmark_return,
                    "round_trip_cost_pct": round_trip_cost,
                    "weight": weight,
                    "horizon": horizon_label,
                    **baselines,
                    "asset_name": STRATEGY_PROXY_LABEL,
                    "decision": "현금" if exposure <= 0 else ("반만 투자" if exposure < 1 else "전액 투자"),
                    "regime": _regime_label(base["data"]),
                    "base_source": base["source"],
                    "future_source": future["source"],
                }
            )
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


def _wilson_lower_bound(success_rate: float, sample_count: int, z: float = 1.96) -> float:
    if sample_count <= 0:
        return 0.0
    p = max(0.0, min(1.0, success_rate))
    n = float(sample_count)
    z2 = z * z
    denom = 1.0 + z2 / n
    center = p + z2 / (2.0 * n)
    margin = z * ((p * (1.0 - p) + z2 / (4.0 * n)) / n) ** 0.5
    lower = (center - margin) / denom
    return max(0.0, min(1.0, lower))


def build_calibration_profile(table_name: str = "", window_days: int = 30) -> Dict[str, Any]:
    history = load_history(table_name=table_name)
    return_rows = _compute_return_rows(history, window_days=window_days)
    if not return_rows:
        return {
            "sample_count": 0,
            "walk_forward_mae": None,
            "walk_forward_direction_hit_rate": None,
            "model": {"slope": 0.0, "intercept": 0.0, "r2": 0.0},
            "verdict_stats": {},
            "score_stats": {},
            "expected_return_pct": 0.0,
            "recommended_position_multiplier": 0.0,
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
        min(1.25, 0.5 + (max(expected_return_pct, 0.0) / 8.0) + (0.2 if final_model["r2"] >= 0.15 else 0.0)),
    )
    weighted_sample_count = sum(final_weights)
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
    }


def build_reliability_guard(calibration: Dict[str, Any], context: str = "crypto") -> Dict[str, Any]:
    sample_count = int(calibration.get("sample_count", 0) or 0)
    mae = calibration.get("walk_forward_mae")
    hit_rate = calibration.get("walk_forward_direction_hit_rate")
    hit_rate_lower = _wilson_lower_bound(float(hit_rate or 0.0), sample_count) if hit_rate is not None else None

    if context == "market":
        min_samples = 5
        caution_samples = 8
        mae_warn = 2.2
        mae_fail = 3.0
        hit_warn = 0.52
        hit_fail = 0.45
        cap_warn = 0.85
        cap_fail = 0.55
    else:
        min_samples = 5
        caution_samples = 12
        mae_warn = 3.2
        mae_fail = 4.5
        hit_warn = 0.50
        hit_fail = 0.42
        cap_warn = 0.80
        cap_fail = 0.50

    if sample_count < min_samples or mae is None or hit_rate is None or hit_rate_lower is None:
        return {
            "state": "미검증",
            "sample_count": sample_count,
            "confidence_delta": 0,
            "position_multiplier_cap": 1.0,
            "max_trade_mode": "실전 후보",
            "note": "표본이 아직 충분하지 않아 자동 보정은 대기합니다.",
        }

    if sample_count < caution_samples:
        if mae >= mae_warn or hit_rate_lower <= hit_warn:
            return {
                "state": "미검증",
                "sample_count": sample_count,
                "confidence_delta": -2,
                "position_multiplier_cap": cap_warn,
                "max_trade_mode": "조건부",
                "note": "표본이 아직 적어서 하한선 기준으로만 보수적 보정을 적용합니다.",
            }
        return {
            "state": "미검증",
            "sample_count": sample_count,
            "confidence_delta": 0,
            "position_multiplier_cap": 1.0,
            "max_trade_mode": "실전 후보",
            "note": "표본이 아직 적어 신뢰성 평가는 계속 쌓는 중입니다.",
        }

    if mae >= mae_fail or hit_rate_lower <= hit_fail:
        return {
            "state": "불안정",
            "sample_count": sample_count,
            "confidence_delta": -8,
            "position_multiplier_cap": cap_fail,
            "max_trade_mode": "관망",
            "note": "walk-forward 하한 성과가 약해 보수적으로 차단합니다.",
        }
    if mae >= mae_warn or hit_rate_lower <= hit_warn:
        return {
            "state": "주의",
            "sample_count": sample_count,
            "confidence_delta": -4,
            "position_multiplier_cap": cap_warn,
            "max_trade_mode": "조건부",
            "note": "walk-forward 하한 성과가 애매해 비중을 줄입니다.",
        }
    return {
        "state": "안정",
        "sample_count": sample_count,
        "confidence_delta": 3,
        "position_multiplier_cap": 1.10,
        "max_trade_mode": "실전 후보",
        "note": "walk-forward 하한 성과가 양호해 자동 보정을 완화합니다.",
    }


def _return_period(rows: List[Dict[str, Any]]) -> Optional[Tuple[str, str]]:
    if not rows:
        return None
    return rows[0]["generated_at"], rows[-1].get("future_generated_at", rows[-1]["generated_at"])


def _return_summary_lines(rows: List[Dict[str, Any]]) -> List[str]:
    if not rows:
        return ["- 평가 가능한 수익률 샘플이 아직 충분하지 않습니다."]
    period = _return_period(rows)
    total_weight = sum(float(row.get("weight", 1.0) or 1.0) for row in rows)
    strategy_capital = [100.0]
    benchmark_capital = [100.0]
    baseline_invest_capital = [100.0]
    baseline_cash_capital = [100.0]
    for row in rows:
        strategy_capital.append(strategy_capital[-1] * (1 + row["strategy_return_pct"] / 100.0))
        benchmark_capital.append(benchmark_capital[-1] * (1 + row["benchmark_return_pct"] / 100.0))
        baseline_invest_capital.append(baseline_invest_capital[-1] * (1 + row["always_invest_return_pct"] / 100.0))
        baseline_cash_capital.append(baseline_cash_capital[-1] * (1 + row["always_cash_return_pct"] / 100.0))
    strategy_total = strategy_capital[-1] - 100.0
    benchmark_total = benchmark_capital[-1] - 100.0
    baseline_invest_total = baseline_invest_capital[-1] - 100.0
    baseline_cash_total = baseline_cash_capital[-1] - 100.0
    win_rate = sum(float(row.get("weight", 1.0) or 1.0) for row in rows if row["strategy_return_pct"] > 0) / max(total_weight, 1.0) * 100
    outperformance = strategy_total - benchmark_total
    lines = [
        f"- 샘플 개수: {len(rows)}",
        f"- 가중 샘플: {total_weight:.1f}",
        f"- 평가 기간: {period[0]} ~ {period[1]}" if period else "- 평가 기간: -",
        f"- 전략 누적 수익률: {_format_pct(strategy_total)}",
        f"- 벤치마크 누적 수익률: {_format_pct(benchmark_total)}",
        f"- 초과 수익률: {_format_pct(outperformance)}",
        f"- 항상 투자 기준: {_format_pct(baseline_invest_total)}",
        f"- 항상 현금 기준: {_format_pct(baseline_cash_total)}",
        f"- 전략 승률: {win_rate:.1f}%",
        f"- 최대 낙폭: {_format_pct(_max_drawdown(strategy_capital))}",
        f"- 투자 비중 규칙: 위험 우위=현금, 주의=50%, 중립~우호=100%",
    ]
    if rows:
        lines.append("- 주요 샘플:")
        for row in rows[:3]:
            lines.append(
                f"  - {row['generated_at']} [{row.get('regime', 'n/a')}] verdict {row['predicted_verdict']} / "
                f"{row['decision']} / 전략 {_format_pct(row['strategy_return_pct'])} "
                f"(cost {_format_pct(row.get('round_trip_cost_pct', 0.0))}) / "
                f"BTC {_format_pct(row['benchmark_return_pct'])}"
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
            weighted_hit = _weighted_mean([float(hit) for hit in hits], weights_for_regime)
            hit_text = f", 판단 적중률 {weighted_hit * 100:.1f}%"
        lines.append(
            f"  - {regime}: 샘플 {len(vals)}, 가중 {sum(weights_for_regime):.1f}, 평균 수익률 {_format_pct(_weighted_mean(vals, weights_for_regime))}{hit_text}"
        )
    return lines


def build_weekly_performance_report(table_name: str = "", window_days: int = 7) -> Dict[str, Any]:
    history = load_history(table_name=table_name)
    rows = _compute_rows(history, window_days=window_days)
    return_rows = _compute_return_rows(history, window_days=window_days)
    calibration = build_calibration_profile(table_name=table_name, window_days=max(window_days, 30))
    reliability = build_reliability_guard(calibration, context="crypto")
    today = dt.datetime.now(REPORT_TIMEZONE).strftime("%Y-%m-%d")
    lines = [
        f"[{today} 사후 검증 리포트 - Crypto Agent]",
        "",
        *_summary_lines(rows),
        "",
        "[수익률 백테스트]",
        *(_return_summary_lines(return_rows)),
        "",
        "[walk-forward 보정]",
        f"- 샘플 개수: {calibration['sample_count']} (원시 {calibration.get('raw_sample_count', 0)}, 가중 {calibration.get('effective_sample_count', 0.0):.1f})",
        f"- 회귀모형: return = {calibration['model']['slope']:+.4f} * score + {calibration['model']['intercept']:+.4f}",
        f"- walk-forward MAE: {calibration['walk_forward_mae']:.2f}%" if calibration["walk_forward_mae"] is not None else "- walk-forward MAE: n/a",
        f"- 방향 적중률: {calibration['walk_forward_direction_hit_rate'] * 100:.1f}%" if calibration["walk_forward_direction_hit_rate"] is not None else "- 방향 적중률: n/a",
        f"- 현재 점수 기준 기대수익: {calibration['expected_return_pct']:+.2f}%",
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
        f"- 비교 대상은 각 리포트 뒤 {MIN_FUTURE_GAP_HOURS}~{MAX_FUTURE_GAP_HOURS}시간 사이에 나온 다음 리포트입니다.",
        "- 판단 적중은 예측 verdict와 실제 verdict가 같았는지로 봅니다.",
        "- 평균 점수 오차는 룰 점수 차이, 구성요소 방향 적중률은 세부 신호가 같은 방향이었는지 봅니다.",
        "- 수익률 백테스트는 리포트 verdict를 따라 BTC 비중을 0/50/100%로 조절했을 때의 결과입니다.",
        "",
        "[읽는 법]",
        "- 적중률이 낮으면 예측 기준이 너무 공격적이거나 기준선이 시장보다 앞서 갔다는 뜻입니다.",
        "- 점수 오차가 크면, 같은 방향이라도 강도를 과하게 잡고 있다는 뜻입니다.",
        "- 수익률 백테스트가 좋지 않으면, 리포트가 맞아도 투자 행동으로는 별로라는 뜻일 수 있습니다.",
        "",
        "주의: 수익률 백테스트는 대표 자산(BTC) 기준의 단순화된 사후 검증입니다.",
    ]
    return {
        "generated_at": dt.datetime.now(REPORT_TIMEZONE).isoformat(timespec="seconds"),
        "project_id": PROJECT_ID,
        "window_days": window_days,
        "rows": rows,
        "return_rows": return_rows,
        "return_period": _return_period(return_rows),
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
