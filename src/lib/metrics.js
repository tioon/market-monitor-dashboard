// 순수 계산 유틸 (REQ-B1/B2/B3, REQ-E1: node --test로 검증 가능하도록 분리)

export const VERDICT_ORDER = ['중립~우호', '주의', '위험 우위'];
export const VERDICT_RANK = Object.fromEntries(VERDICT_ORDER.map((label, index) => [label, index]));

// 백엔드 룰 임계값과 동일한 기본값 (score >= 6 위험 우위, >= 3 주의)
export const DEFAULT_LOW_THRESHOLD = 3;
export const DEFAULT_HIGH_THRESHOLD = 6;
export const MIN_TRAIN_SAMPLES = 5;

export function toNumber(value, fallback = 0) {
  const number = Number(value);
  return Number.isFinite(number) ? number : fallback;
}

function cleanValue(value) {
  if (value === null || value === undefined) return '';
  const text = String(value).trim();
  return text === '' || text === 'null' || text === 'undefined' ? '' : text;
}

export function normalizeVerdict(value) {
  const text = cleanValue(value);
  if (!text) return null;
  if (VERDICT_RANK[text] !== undefined) return text;
  if (text.includes('위험')) return '위험 우위';
  if (text.includes('주의') || text.includes('혼조')) return '주의';
  if (text.includes('우호') || text.includes('중립')) return '중립~우호';
  return null;
}

export function verdictRank(value) {
  const normalized = normalizeVerdict(value);
  return normalized === null ? null : VERDICT_RANK[normalized];
}

export function verdictDistance(predicted, actual) {
  const predRank = verdictRank(predicted);
  const actualRank = verdictRank(actual);
  if (predRank === null || actualRank === null) return null;
  return Math.abs(predRank - actualRank);
}

export function verdictCost(predicted, actual) {
  const distance = verdictDistance(predicted, actual);
  if (distance === null) return null;
  if (distance === 0) return 0;
  if (distance === 1) return 1;
  return 3;
}

export function scoreToVerdict(score, lowThreshold, highThreshold) {
  const value = toNumber(score, 0);
  if (value >= highThreshold) return '위험 우위';
  if (value >= lowThreshold) return '주의';
  return '중립~우호';
}

// REQ-B2: 이항 비율의 Wilson 신뢰구간
export function wilsonInterval(successRate, sampleCount, z = 1.96) {
  if (!sampleCount || sampleCount <= 0) return [0, 1];
  const p = Math.max(0, Math.min(1, successRate));
  const n = sampleCount;
  const z2 = z * z;
  const denom = 1 + z2 / n;
  const center = p + z2 / (2 * n);
  const margin = z * Math.sqrt((p * (1 - p) + z2 / (4 * n)) / n);
  return [Math.max(0, (center - margin) / denom), Math.min(1, (center + margin) / denom)];
}

export function optimizeVerdictThresholds(rows) {
  const scores = [...new Set((Array.isArray(rows) ? rows : []).map((row) => toNumber(row?.predicted_score, NaN)).filter(Number.isFinite))].sort((a, b) => a - b);
  if (!scores.length) return null;

  const candidates = new Set([scores[0] - 1, scores.at(-1) + 1]);
  for (let index = 0; index < scores.length - 1; index += 1) {
    candidates.add((scores[index] + scores[index + 1]) / 2);
  }

  const candidateList = [...candidates].sort((a, b) => a - b);
  let best = null;

  for (const low of candidateList) {
    for (const high of candidateList) {
      if (!(low < high)) continue;

      let exactHits = 0;
      let weightedCost = 0;
      let mildMisses = 0;
      let strongMisses = 0;
      let counted = 0;

      for (const row of rows) {
        const actual = normalizeVerdict(row?.actual_verdict);
        if (actual === null) continue; // REQ-B3: 실측값 없는 행 제외
        counted += 1;
        const predicted = scoreToVerdict(row?.predicted_score, low, high);
        const distance = verdictDistance(predicted, actual);
        if (distance === 0) exactHits += 1;
        else if (distance === 1) mildMisses += 1;
        else if (distance === 2) strongMisses += 1;
        weightedCost += verdictCost(predicted, actual) ?? 0;
      }
      if (!counted) continue;

      const candidate = {
        low,
        high,
        exactHits,
        exactAccuracy: exactHits / counted,
        weightedAccuracy: 1 - weightedCost / (counted * 3),
        mildMisses,
        strongMisses,
        weightedCost,
      };

      if (
        !best ||
        candidate.weightedCost < best.weightedCost ||
        (candidate.weightedCost === best.weightedCost && candidate.exactHits > best.exactHits) ||
        (candidate.weightedCost === best.weightedCost && candidate.exactHits === best.exactHits && candidate.strongMisses < best.strongMisses) ||
        (candidate.weightedCost === best.weightedCost && candidate.exactHits === best.exactHits && candidate.strongMisses === best.strongMisses && candidate.low > best.low)
      ) {
        best = candidate;
      }
    }
  }

  return best;
}

const EMPTY_CONFUSION = () => ({
  '중립~우호': { '중립~우호': 0, 주의: 0, '위험 우위': 0 },
  주의: { '중립~우호': 0, 주의: 0, '위험 우위': 0 },
  '위험 우위': { '중립~우호': 0, 주의: 0, '위험 우위': 0 },
});

/**
 * REQ-B1: walk-forward(OOS) 평가.
 * 각 행의 판정 임계값은 그 행 "이전" 데이터로만 산출한다.
 * 훈련 표본이 부족하면 백엔드 룰 기본값(3/6)을 사용한다.
 * REQ-B3: actual_verdict가 없는 행은 분모에서 제외한다.
 */
export function evaluateVerdictRowsWalkForward(rows, options = {}) {
  const {
    minTrain = MIN_TRAIN_SAMPLES,
    defaultLow = DEFAULT_LOW_THRESHOLD,
    defaultHigh = DEFAULT_HIGH_THRESHOLD,
  } = options;
  const normalizedRows = Array.isArray(rows) ? rows : [];
  const ordered = [...normalizedRows].sort((a, b) => String(a?.generated_at).localeCompare(String(b?.generated_at)));
  const usable = ordered.filter((row) => normalizeVerdict(row?.actual_verdict) !== null);

  const confusion = EMPTY_CONFUSION();
  let exactHits = 0;
  let weightedCost = 0;
  let mildMisses = 0;
  let strongMisses = 0;
  let componentHitSum = 0;
  let componentTotalSum = 0;
  let walkForwardCount = 0;

  usable.forEach((row, index) => {
    let low = defaultLow;
    let high = defaultHigh;
    const train = usable.slice(0, index);
    if (train.length >= minTrain) {
      const fitted = optimizeVerdictThresholds(train);
      if (fitted) {
        low = fitted.low;
        high = fitted.high;
        walkForwardCount += 1;
      }
    }
    const predicted = scoreToVerdict(row?.predicted_score, low, high);
    const actual = normalizeVerdict(row?.actual_verdict);
    confusion[predicted][actual] += 1;
    const distance = verdictDistance(predicted, actual);
    if (distance === 0) exactHits += 1;
    else if (distance === 1) mildMisses += 1;
    else if (distance === 2) strongMisses += 1;
    weightedCost += verdictCost(predicted, actual) ?? 0;
    componentHitSum += toNumber(row?.component_hits, 0);
    componentTotalSum += Math.max(toNumber(row?.component_total, 0), toNumber(row?.component_hits, 0));
  });

  const count = usable.length;
  const exactAccuracy = count ? exactHits / count : 0;
  const [accuracyCiLow, accuracyCiHigh] = wilsonInterval(exactAccuracy, count);
  // 오늘 판정용 임계값: 전체 과거 데이터로 적합 (오늘의 실측은 미포함이므로 look-ahead 아님)
  const bestThresholds = count >= minTrain ? optimizeVerdictThresholds(usable) : null;

  return {
    count,
    excludedCount: normalizedRows.length - count,
    exactHits,
    exactAccuracy,
    accuracyCiLow,
    accuracyCiHigh,
    weightedCost,
    weightedAccuracy: count ? 1 - weightedCost / (count * 3) : 0,
    mildMisses,
    strongMisses,
    componentHitRate: componentTotalSum > 0 ? componentHitSum / componentTotalSum : 0,
    confusion,
    bestThresholds,
    method: 'walk-forward',
    walkForwardCoverage: count ? walkForwardCount / count : 0,
  };
}

export function summarizeReturnRows(rows) {
  const normalized = Array.isArray(rows) ? rows : [];
  return normalized.reduce(
    (acc, row) => {
      acc.strategy += toNumber(row?.strategy_return_pct, 0);
      acc.benchmark += toNumber(
        row?.benchmark_return_pct ?? row?.spy_return_pct ?? row?.kospi_return_pct ?? row?.asset_return_pct,
        0
      );
      acc.exposure += toNumber(row?.exposure, 0);
      acc.count += 1;
      return acc;
    },
    { strategy: 0, benchmark: 0, exposure: 0, count: 0 }
  );
}

export function computeMaxDrawdownPct(rows, key = 'strategy_return_pct') {
  const normalized = Array.isArray(rows) ? rows : [];
  let capital = 1;
  let peak = 1;
  let maxDrawdown = 0;

  for (const row of normalized) {
    capital *= 1 + toNumber(row?.[key], 0) / 100;
    peak = Math.max(peak, capital);
    const drawdown = (capital / peak - 1) * 100;
    maxDrawdown = Math.min(maxDrawdown, drawdown);
  }

  return maxDrawdown;
}

export function applyVerdictHysteresis(rows, lowThreshold, highThreshold, margin = 0.4) {
  const ordered = [...rows].sort((a, b) => String(a.generated_at).localeCompare(String(b.generated_at)));
  const recent = ordered.slice(-2);
  if (!recent.length) return null;

  const latest = scoreToVerdict(recent.at(-1)?.predicted_score, lowThreshold, highThreshold);
  if (recent.length === 1) return latest;

  const previous = scoreToVerdict(recent.at(-2)?.predicted_score, lowThreshold, highThreshold);
  if (latest === previous) return latest;

  const latestScore = toNumber(recent.at(-1)?.predicted_score, 0);
  const distanceToLow = Math.abs(latestScore - lowThreshold);
  const distanceToHigh = Math.abs(latestScore - highThreshold);
  if (Math.min(distanceToLow, distanceToHigh) <= margin) {
    return previous;
  }

  return latest;
}

// REQ-C1: 백엔드가 계산한 신뢰 등급 추출 (프론트 재계산 금지, 표시만)
export function extractTrustGrade(latestDecisionSnapshot, performanceReportJson) {
  return (
    latestDecisionSnapshot?.trust_grade ||
    latestDecisionSnapshot?.calibration?.trust_grade ||
    performanceReportJson?.trust_grade ||
    performanceReportJson?.calibration?.trust_grade ||
    null
  );
}

export function trustBadge(trust) {
  if (!trust || !trust.grade) {
    return {
      grade: '검증 중',
      usable: false,
      detail: '등급 정보 없음 (백엔드 v2 산출 전) — 실전 참고 불가',
    };
  }
  const n = toNumber(trust.sample_count, 0);
  if (!trust.usable_for_trading) {
    const reason = (trust.unmet_conditions || []).slice(0, 2).join('; ') || '기준 미충족';
    return { grade: trust.grade, usable: false, detail: `표본 ${n} · ${reason} — 실전 참고 불가` };
  }
  const hit = trust.oos_hit_rate;
  const lower = trust.oos_hit_lower_bound;
  const hitText = hit !== null && hit !== undefined && lower !== null && lower !== undefined
    ? `OOS 적중률 ${(hit * 100).toFixed(0)}% (하한 ${(lower * 100).toFixed(0)}%)`
    : 'OOS 적중률 n/a';
  return { grade: trust.grade, usable: true, detail: `표본 ${n} · ${hitText}` };
}
