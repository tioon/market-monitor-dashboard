import React from 'react';
import snapshot from './data/snapshot.json';

function dayOnly(value) {
  if (!value) return '-';
  return String(value).slice(0, 10);
}

function cleanText(value, fallback = '-') {
  if (value === null || value === undefined) return fallback;
  const text = String(value).trim();
  return text && text.toLowerCase() !== 'unknown' ? text : fallback;
}

function isEmptyValue(value) {
  return value === null || value === undefined || String(value).trim() === '';
}

function toNumber(value, fallback = 0) {
  const parsed = Number(value);
  return Number.isFinite(parsed) ? parsed : fallback;
}

function pct(value, digits = 1) {
  const number = toNumber(value, 0);
  return `${number >= 0 ? '+' : ''}${number.toFixed(digits)}%`;
}

function rateText(value, digits = 1) {
  return `${toNumber(value, 0).toFixed(digits)}%`;
}

function currency(value) {
  return new Intl.NumberFormat('ko-KR', {
    style: 'currency',
    currency: 'KRW',
    maximumFractionDigits: 0,
  }).format(value);
}

function compactCurrency(value) {
  return new Intl.NumberFormat('ko-KR', {
    notation: 'compact',
    maximumFractionDigits: 1,
  }).format(value);
}

function verdictTone(verdict) {
  if (!verdict) return 'neutral';
  if (verdict.includes('위험')) return 'danger';
  if (verdict.includes('주의') || verdict.includes('혼조')) return 'caution';
  return 'good';
}

function verdictLabel(verdict) {
  return cleanText(verdict, '미정');
}

function formatPercentInt(value) {
  const text = cleanText(value, '-');
  return text === '-' ? text : `${text}%`;
}

const VERDICT_ORDER = ['중립~우호', '주의', '위험 우위'];
const VERDICT_RANK = Object.fromEntries(VERDICT_ORDER.map((label, index) => [label, index]));

function normalizeVerdict(value) {
  const text = cleanText(value, '');
  if (!text) return null;
  if (VERDICT_RANK[text] !== undefined) return text;
  if (text.includes('위험')) return '위험 우위';
  if (text.includes('주의') || text.includes('혼조')) return '주의';
  if (text.includes('우호') || text.includes('중립')) return '중립~우호';
  return null;
}

function verdictRank(value) {
  const normalized = normalizeVerdict(value);
  return normalized === null ? null : VERDICT_RANK[normalized];
}

function verdictDistance(predicted, actual) {
  const predRank = verdictRank(predicted);
  const actualRank = verdictRank(actual);
  if (predRank === null || actualRank === null) return null;
  return Math.abs(predRank - actualRank);
}

function verdictCost(predicted, actual) {
  const distance = verdictDistance(predicted, actual);
  if (distance === null) return null;
  if (distance === 0) return 0;
  if (distance === 1) return 1;
  return 3;
}

function parseReportVerdict(reportText) {
  const match = String(reportText || '').match(/판단:\s*([^|\n]+)/);
  return match ? normalizeVerdict(match[1]) : null;
}

function scoreToVerdict(score, lowThreshold, highThreshold) {
  const value = toNumber(score, 0);
  if (value >= highThreshold) return '위험 우위';
  if (value >= lowThreshold) return '주의';
  return '중립~우호';
}

function summarizeReturnRows(rows) {
  const normalized = Array.isArray(rows) ? rows : [];
  return normalized.reduce(
    (acc, row) => {
      acc.strategy += toNumber(row?.strategy_return_pct, 0);
      acc.benchmark += toNumber(
        row?.benchmark_return_pct ??
          row?.spy_return_pct ??
          row?.kospi_return_pct ??
          row?.asset_return_pct,
        0
      );
      acc.exposure += toNumber(row?.exposure, 0);
      acc.count += 1;
      return acc;
    },
    { strategy: 0, benchmark: 0, exposure: 0, count: 0 }
  );
}

function computeMaxDrawdownPct(rows, key = 'strategy_return_pct') {
  const normalized = Array.isArray(rows) ? rows : [];
  let capital = 1;
  let peak = 1;
  let maxDrawdown = 0;

  for (const row of normalized) {
    capital *= 1 + toNumber(row?.[key], 0) / 100;
    peak = Math.max(peak, capital);
    const drawdown = ((capital / peak) - 1) * 100;
    maxDrawdown = Math.min(maxDrawdown, drawdown);
  }

  return maxDrawdown;
}

function applyVerdictHysteresis(rows, lowThreshold, highThreshold, margin = 0.4) {
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

function optimizeVerdictThresholds(rows) {
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

      for (const row of rows) {
        const predicted = scoreToVerdict(row?.predicted_score, low, high);
        const actual = normalizeVerdict(row?.actual_verdict);
        const distance = verdictDistance(predicted, actual);
        if (distance === 0) exactHits += 1;
        else if (distance === 1) mildMisses += 1;
        else if (distance === 2) strongMisses += 1;
        weightedCost += verdictCost(predicted, actual) ?? 0;
      }

      const candidate = {
        low,
        high,
        exactHits,
        exactAccuracy: exactHits / rows.length,
        weightedAccuracy: 1 - weightedCost / (rows.length * 3),
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

function latestDecision(project) {
  return project.latestDecision?.decision_snapshot || null;
}

function latestReport(project) {
  return project.latestReport || null;
}

function performanceSummary(project) {
  const report = project.performance?.report_json || {};
  const evaluationRows = Array.isArray(report.rows) ? report.rows : [];
  const returnRows = Array.isArray(report.return_rows) ? report.return_rows : [];
  const calibration = report.calibration || {};
  const benchmarkWeights = calibration.benchmark_weights || {};
  const isMarket = project.projectId === 'market-agent';
  const leftLabel = isMarket ? 'S&P 500' : 'BTC';
  const rightLabel = isMarket ? 'KOSPI' : 'ETH';
  const leftWeight = toNumber(benchmarkWeights.left_weight, 0.5);
  const rightWeight = toNumber(benchmarkWeights.right_weight, 0.5);
  const evaluation = evaluateVerdictRows(evaluationRows);
  const latestDecisionData = latestDecision(project) || {};
  const latestScore = toNumber(
    latestDecisionData.dashboard?.score ??
      latestDecisionData.ai_signal?.rule_score ??
      latestDecisionData.ai_signal?.combined_score ??
      evaluationRows.at(-1)?.predicted_score,
    0
  );
  const latestThresholds = evaluation.bestThresholds;
  const tunedVerdict = latestThresholds
    ? scoreToVerdict(latestScore, latestThresholds.low, latestThresholds.high)
    : normalizeVerdict(latestDecisionData.dashboard?.verdict || latestDecisionData.ai_signal?.verdict);
  const hysteresisVerdict = latestThresholds
    ? applyVerdictHysteresis(evaluationRows, latestThresholds.low, latestThresholds.high)
    : tunedVerdict;
  const guard = project.performance?.report_json?.reliability_guard || {};
  const r2 = toNumber(latestDecisionData.calibration?.model?.r2, 0);
  const recommendedGuard = buildAdaptiveGuard({
    exactAccuracy: evaluation.exactAccuracy,
    weightedAccuracy: evaluation.weightedAccuracy,
    strongMissRate: evaluation.count ? evaluation.strongMisses / evaluation.count : 0,
    r2,
    currentGuard: guard,
  });
  const returns = summarizeReturnRows(returnRows);
  const strategyReturn = returns.count ? pct(returns.strategy, 2) : '-';
  const benchmarkReturn = returns.count ? pct(returns.benchmark, 2) : '-';
  const maxDrawdown = returnRows.length ? computeMaxDrawdownPct(returnRows, 'strategy_return_pct') : 0;

  return {
    exactAccuracy: evaluation.exactAccuracy,
    exactAccuracyText: rateText(evaluation.exactAccuracy * 100, 1),
    weightedAccuracy: evaluation.weightedAccuracy,
    weightedAccuracyText: rateText(evaluation.weightedAccuracy * 100, 1),
    mildMissRate: evaluation.count ? evaluation.mildMisses / evaluation.count : 0,
    mildMissRateText: rateText((evaluation.count ? evaluation.mildMisses / evaluation.count : 0) * 100, 1),
    strongMissRate: evaluation.count ? evaluation.strongMisses / evaluation.count : 0,
    strongMissRateText: rateText((evaluation.count ? evaluation.strongMisses / evaluation.count : 0) * 100, 1),
    componentHitRate: evaluation.componentHitRate,
    componentHitRateText: rateText(evaluation.componentHitRate * 100, 1),
    confusion: evaluation.confusion,
    thresholdLow: latestThresholds?.low ?? null,
    thresholdHigh: latestThresholds?.high ?? null,
    thresholdText: latestThresholds ? `${latestThresholds.low.toFixed(2)} / ${latestThresholds.high.toFixed(2)}` : '-',
    tunedVerdict,
    hysteresisVerdict,
    recommendedGuard,
    strategyReturn,
    benchmarkReturn,
    maxDrawdownText: returnRows.length ? pct(maxDrawdown, 2) : '-',
    sampleCount: evaluation.count,
    returnSampleCount: returnRows.length,
    exposureRate: returnRows.length ? returnRows.reduce((sum, row) => sum + toNumber(row.exposure, 0), 0) / returnRows.length : null,
    benchmarkWeightsLabel: `${leftLabel} ${Math.round(leftWeight * 100)}% / ${rightLabel} ${Math.round(rightWeight * 100)}%`,
    benchmarkMethod: benchmarkWeights.method || '-',
  };
}

function compoundReturnPct(finalCapital, initialCapital) {
  const base = Math.max(toNumber(initialCapital, 0), 1);
  return ((toNumber(finalCapital, base) / base) - 1) * 100;
}

function calibrationWarning(project) {
  const decision = latestDecision(project);
  const calibration = decision?.calibration || {};
  const reportRows = Array.isArray(project.performance?.report_json?.rows) ? project.performance.report_json.rows : [];
  const uniqueScores = new Set(
    reportRows
      .map((row) => row?.predicted_score)
      .filter((value) => value !== null && value !== undefined && value !== '')
  ).size;
  const sampleCount = toNumber(calibration.sample_count ?? reportRows.length, 0);
  const r2 = toNumber(calibration.model?.r2, 0);
  const evaluation = evaluateVerdictRows(reportRows);
  const issues = [];

  if (sampleCount < 3) {
    issues.push(`샘플이 너무 적습니다(${sampleCount}개).`);
  }
  if (uniqueScores < 2) {
    issues.push(`예측 점수가 거의 고정되어 있습니다(고유 score ${uniqueScores}개).`);
  }
  if (r2 < 0.05) {
    issues.push(`회귀 설명력이 낮습니다(R² ${r2.toFixed(2)}).`);
  }
  if (evaluation.weightedAccuracy < 0.7) {
    issues.push(`비용가중 적중률이 낮습니다(${rateText(evaluation.weightedAccuracy * 100, 1)}).`);
  }
  if (evaluation.strongMisses > 0) {
    issues.push(`강한 오분류가 ${evaluation.strongMisses}회 있습니다.`);
  }

  if (!issues.length) return null;

  return `최근 보정값은 예측력보다 보수적 가드가 더 필요합니다. ${issues.join(' ')}`;
}

function buildAdaptiveGuard({ exactAccuracy, weightedAccuracy, strongMissRate, r2, currentGuard = {} }) {
  const currentCap = toNumber(currentGuard.position_multiplier_cap, 1);
  const currentMode = cleanText(currentGuard.max_trade_mode, '실전 후보');

  if ((weightedAccuracy < 0.6 && r2 < 0.05) || strongMissRate >= 0.3) {
    return {
      state: '보류',
      position_multiplier_cap: Math.min(currentCap, 0.5),
      max_trade_mode: '보류',
      note: '비용가중 적중률 또는 회귀 설명력이 너무 낮아 자동 보류합니다.',
    };
  }

  if (weightedAccuracy < 0.75 || exactAccuracy < 0.35 || r2 < 0.1) {
    return {
      state: '관망',
      position_multiplier_cap: Math.min(currentCap, 0.55),
      max_trade_mode: '관망',
      note: '약한 오분류가 많아 관망 수준으로 축소합니다.',
    };
  }

  if (weightedAccuracy < 0.85 || exactAccuracy < 0.55) {
    return {
      state: '조건부',
      position_multiplier_cap: Math.min(currentCap, 0.85),
      max_trade_mode: '조건부',
      note: '절대 정확도는 부족하지만 비용가중 성능이 버텨 조건부 대응합니다.',
    };
  }

  return {
    state: currentMode,
    position_multiplier_cap: currentCap,
    max_trade_mode: currentMode,
    note: '현재 가드 기준을 유지합니다.',
  };
}

function slugId(value) {
  return String(value || '').replace(/[^a-z0-9]+/gi, '-').toLowerCase();
}

const PROJECT_CORE_PRIORITIES = {
  'market-agent': ['kospi', 'kosdaq', 'usdkrw', 'us10y', 'dxy', 'vix', 'sp500_pe', 'shiller_pe', 'earnings_yield', 't10y2y', 'spy_1mo_pct', 'rsp_1mo_pct', 'iwm_1mo_pct', 'hyg_1mo_pct'],
  'crypto-agent': ['btc_24h', 'eth_24h', 'btc_7d', 'eth_fee_7d', 'btc_active_7d', 'eth_tx_7d', 'fear_greed', 'stablecoin_supply', 'mcap'],
};

const PROJECT_LEVEL_PRIORITIES = {
  'market-agent': ['kospi', 'kosdaq', 'usdkrw', 'us10y', 'dxy', 'vix', 'sp500_pe', 'shiller_pe', 'earnings_yield', 't10y2y'],
  'crypto-agent': ['btc_price', 'eth_price', 'btc_dom', 'eth_dom', 'fng_value', 'funding_btc', 'funding_eth', 'btc_sma20', 'btc_sma50', 'btc_sma200'],
};

const PROJECT_CORE_LABELS = {
  'market-agent': {
    kospi: 'KOSPI',
    kosdaq: 'KOSDAQ',
    usdkrw: 'USD/KRW',
    us10y: 'US 10Y',
    dxy: 'DXY',
    vix: 'VIX',
    sp500_pe: 'S&P500 P/E',
    shiller_pe: 'CAPE',
    earnings_yield: 'Earnings Yield',
    t10y2y: '10Y-2Y',
    spy_1mo_pct: 'SPY 1개월',
    rsp_1mo_pct: 'RSP 1개월',
    iwm_1mo_pct: 'IWM 1개월',
    hyg_1mo_pct: 'HYG 1개월',
  },
  'crypto-agent': {
    btc_24h: 'BTC 24h',
    eth_24h: 'ETH 24h',
    btc_7d: 'BTC 7d',
    eth_fee_7d: 'ETH fee 7d',
    btc_active_7d: 'BTC active 7d',
    eth_tx_7d: 'ETH tx 7d',
    fear_greed: 'Fear & Greed',
    stablecoin_supply: 'Stablecoin supply',
    mcap: 'Market cap',
  },
};

function projectCoreLabel(projectId, key) {
  return PROJECT_CORE_LABELS[projectId]?.[key] || key;
}

function buildLevelRows(projectId, currentCoreData, previousCoreData) {
  return (PROJECT_LEVEL_PRIORITIES[projectId] || [])
    .map((key) => {
      const current = currentCoreData?.[key];
      const previous = previousCoreData?.[key];
      if (isEmptyValue(current) || isEmptyValue(previous)) return null;
      const currentText = formatCoreValue(key, current);
      const previousText = formatCoreValue(key, previous);
      if (currentText === previousText) return null;
      return {
        key,
        label: projectCoreLabel(projectId, key),
        currentText,
        previousText,
      };
    })
    .filter(Boolean)
    .slice(0, 8);
}

function reportRows(project) {
  return (project.decisions || [])
    .map((item) => {
      const snap = item.decision_snapshot || {};
      const ai = snap.ai_signal || {};
      const dashboard = snap.dashboard || {};
      const engine = snap.engine || {};
      return {
        day: dayOnly(item.generated_at),
        generated_at: item.generated_at,
        verdict: dashboard.verdict || ai.verdict || ai.ai_verdict || null,
        score: dashboard.score ?? ai.rule_score ?? ai.combined_score ?? null,
        confidence: engine.confidence_score ?? ai.confidence ?? ai.ai_confidence ?? null,
        position: engine.position_size ?? ai.position_size ?? null,
      };
    })
    .sort((a, b) => (a.day < b.day ? -1 : 1));
}

function mergeDailyRows(marketProject, cryptoProject) {
  const marketMap = new Map(reportRows(marketProject).map((row) => [row.day, row]));
  const cryptoMap = new Map(reportRows(cryptoProject).map((row) => [row.day, row]));
  const days = [...new Set([...marketMap.keys(), ...cryptoMap.keys()])].sort();

  return days.map((day) => ({
    day,
    market: marketMap.get(day) || null,
    crypto: cryptoMap.get(day) || null,
  }));
}

function buildTrendSeries(rows, key) {
  return {
    labels: rows.map((row) => row.day.slice(5)),
    market: rows.map((row) => toNumber(row.market?.[key], 0)),
    crypto: rows.map((row) => toNumber(row.crypto?.[key], 0)),
  };
}

function projectLabel(projectId) {
  return projectId === 'market-agent' ? 'Market Agent' : 'Crypto Agent';
}

function getReturnRows(project) {
  const history = Array.isArray(project.performance_history) ? project.performance_history : [];
  const historicalRows = history.flatMap((item) => {
    const rows = Array.isArray(item?.return_rows) ? item.return_rows : Array.isArray(item?.report_json?.return_rows) ? item.report_json.return_rows : [];
    return Array.isArray(rows) ? rows : [];
  });

  if (historicalRows.length) {
    const byDay = new Map();
    for (const row of historicalRows) {
      const key = dayOnly(row?.generated_at);
      if (!key) continue;
      byDay.set(key, row);
    }
    return [...byDay.values()].sort((a, b) => String(a.generated_at).localeCompare(String(b.generated_at)));
  }

  const rows = project.performance?.report_json?.return_rows;
  return Array.isArray(rows) ? rows : [];
}

function mergeReturnRows(marketProject, cryptoProject) {
  const marketRows = getReturnRows(marketProject);
  const cryptoRows = getReturnRows(cryptoProject);
  const marketMap = new Map(marketRows.map((row) => [dayOnly(row.generated_at), row]));
  const cryptoMap = new Map(cryptoRows.map((row) => [dayOnly(row.generated_at), row]));
  const days = [...new Set([...marketMap.keys(), ...cryptoMap.keys()])].sort();

  return days.map((day) => ({
    day,
    market: marketMap.get(day) || null,
    crypto: cryptoMap.get(day) || null,
  }));
}

function buildReportLedger(project) {
  const decisionMap = new Map(
    (project.decisions || []).map((item) => [dayOnly(item.generated_at), item.decision_snapshot || null])
  );
  const performanceMap = new Map();
  for (const item of Array.isArray(project.performance_history) ? project.performance_history : []) {
    const rows = Array.isArray(item?.return_rows) ? item.return_rows : Array.isArray(item?.report_json?.return_rows) ? item.report_json.return_rows : [];
    if (!Array.isArray(rows)) continue;
    for (const row of rows) {
      const key = dayOnly(row.generated_at);
      if (!key) continue;
      performanceMap.set(key, row);
    }
  }
  if (!performanceMap.size) {
    for (const row of getReturnRows(project)) {
      const key = dayOnly(row.generated_at);
      if (!key) continue;
      performanceMap.set(key, row);
    }
  }

  return (project.reports || [])
    .map((report, index) => {
      const day = dayOnly(report.generated_at);
      const decision = decisionMap.get(day) || null;
      const performance = performanceMap.get(day) || null;
      const dashboard = decision?.dashboard || {};
      const engine = decision?.engine || {};
      const ai = decision?.ai_signal || {};
      const calibration = decision?.calibration || {};
      return {
        index,
        day,
        report,
        decision,
        performance,
        verdict: dashboard.verdict || ai.verdict || ai.ai_verdict || null,
        score: dashboard.score ?? ai.rule_score ?? ai.combined_score ?? null,
        confidence: engine.confidence_score ?? ai.confidence ?? ai.ai_confidence ?? null,
        position: engine.position_size ?? ai.position_size ?? null,
        tradeMode: engine.trade_mode || ai.trade_mode || null,
        sampleCount: calibration.sample_count ?? calibration.raw_sample_count ?? 0,
      };
    })
    .sort((a, b) => (a.report.generated_at < b.report.generated_at ? -1 : 1));
}

function reportKeyMetrics(project) {
  const latest = latestDecision(project) || {};
  const performance = performanceSummary(project) || {};
  const recommended = performance.recommendedGuard || {};
  return [
    {
      label: '최근 판정',
      value: verdictLabel(latest.dashboard?.verdict || latest.ai_signal?.verdict),
      caption: `보정 ${verdictLabel(performance.hysteresisVerdict || performance.tunedVerdict)}`,
    },
    {
      label: '정확도',
      value: performance.exactAccuracyText || '-',
      caption: `비용가중 ${performance.weightedAccuracyText || '-'}`,
    },
    {
      label: '오분류',
      value: `${performance.mildMissRateText || '-'} / ${performance.strongMissRateText || '-'}`,
      caption: `세부신호 ${performance.componentHitRateText || '-'}`,
    },
    {
      label: '권장 가드',
      value: recommended.max_trade_mode || '미정',
      caption: `${recommended.note || '가드 정보 없음'} · cap x${toNumber(recommended.position_multiplier_cap, 1).toFixed(2)}`,
    },
  ];
}

function summarizeCoreData(coreData, projectId) {
  const entries = Object.entries(coreData || {})
    .filter(([, value]) => value !== null && value !== undefined)
    .map(([key, value]) => ({
      key,
      value,
      priority: 0,
    }));

  const priorityKeys = projectId === 'market-agent'
    ? ['spy_1mo_pct', 'rsp_1mo_pct', 'iwm_1mo_pct', 'hyg_1mo_pct', 'vix', 'usdkrw', 'us10y', 'shiller_pe', 'earnings_yield', 'kospi']
    : ['btc_24h', 'eth_24h', 'btc_7d', 'eth_fee_7d', 'btc_active_7d', 'eth_tx_7d', 'fear_greed', 'stablecoin_supply', 'mcap'];

  return entries
    .map((item) => ({
      ...item,
      priority: priorityKeys.indexOf(item.key) === -1 ? priorityKeys.length : priorityKeys.indexOf(item.key),
    }))
    .sort((a, b) => a.priority - b.priority || a.key.localeCompare(b.key))
    .slice(0, 10);
}

function formatCoreValue(key, value) {
  if (value === null || value === undefined) return '-';
  if (typeof value === 'number') {
    if (key.includes('pct') || key.includes('rate') || key.includes('score') || key.includes('hit') || key.includes('yield') || key.includes('spread') || key.includes('fee') || key.includes('return')) {
      return pct(value, 2);
    }
    if (key.includes('vix') || key.includes('fear') || key.includes('r2')) {
      return toNumber(value, 0).toFixed(2);
    }
    return new Intl.NumberFormat('ko-KR', { maximumFractionDigits: 2 }).format(value);
  }
  return String(value);
}

function ReportExplorer({ marketProject, cryptoProject }) {
  const analysisCapital = 10000000;
  const [activeProjectId, setActiveProjectId] = React.useState(marketProject.projectId);
  const activeProject = activeProjectId === cryptoProject.projectId ? cryptoProject : marketProject;
  const ledgerRows = React.useMemo(() => buildReportLedger(activeProject), [activeProject]);
  const ledgerKey = React.useMemo(() => ledgerRows.map((row) => row.day).join('|'), [ledgerRows]);
  const [selectedDay, setSelectedDay] = React.useState(ledgerRows.at(-1)?.day || '');

  React.useEffect(() => {
    const hasSelected = ledgerRows.some((row) => row.day === selectedDay);
    if (!hasSelected) {
      setSelectedDay(ledgerRows.at(-1)?.day || '');
    }
  }, [ledgerKey, selectedDay, ledgerRows]);

  const selectedIndex = ledgerRows.findIndex((row) => row.day === selectedDay);
  const selectedRow = selectedIndex >= 0 ? ledgerRows[selectedIndex] : ledgerRows.at(-1) || null;
  const projectPerf = performanceSummary(activeProject) || {};
  const metrics = reportKeyMetrics(activeProject);
  const projectGuard = projectPerf.recommendedGuard || {};
  const tunedProjectVerdict = verdictLabel(projectPerf.hysteresisVerdict || projectPerf.tunedVerdict);
  const coreData = summarizeCoreData(selectedRow?.decision?.core_data || {}, activeProject.projectId);
  const previousRow = selectedIndex > 0 ? ledgerRows[selectedIndex - 1] : null;
  const levelRows = buildLevelRows(
    activeProject.projectId,
    selectedRow?.decision?.core_data || {},
    previousRow?.decision?.core_data || {}
  );
  const reportText = selectedRow?.report?.report_text || '리포트 본문이 없습니다.';
  const brief = selectedRow?.decision?.decision_brief || [];
  const evidence = selectedRow?.decision?.core_evidence || [];
  const positives = selectedRow?.decision?.dashboard?.positives || [];
  const negatives = selectedRow?.decision?.dashboard?.negatives || [];
  const perf = selectedRow?.performance || null;
  const performanceTone = perf?.strategy_return_pct >= 0 ? 'good' : 'danger';
  const reportNetPnl = perf ? pnlFromReturnPct(perf.strategy_return_pct, analysisCapital) : null;
  const benchmarkNetPnl = perf ? pnlFromReturnPct(perf.benchmark_return_pct, analysisCapital) : null;
  const reportPnl = perf ? formatNetPnl(reportNetPnl) : null;
  const benchmarkPnl = perf ? formatNetPnl(benchmarkNetPnl) : null;

  return (
    <section className="panel report-explorer">
      <div className="section-head">
        <div>
          <div className="eyebrow">Report explorer</div>
          <h3>리포트 분석 화면</h3>
          <p className="subtle">DynamoDB 리포트, decision, 24시간 사후검증 성과를 한 화면에서 맞춰 봅니다.</p>
        </div>
        <div className="report-tabs">
          <button
            type="button"
            className={activeProjectId === marketProject.projectId ? 'report-tab active' : 'report-tab'}
            onClick={() => setActiveProjectId(marketProject.projectId)}
          >
            Market
          </button>
          <button
            type="button"
            className={activeProjectId === cryptoProject.projectId ? 'report-tab active' : 'report-tab'}
            onClick={() => setActiveProjectId(cryptoProject.projectId)}
          >
            Crypto
          </button>
        </div>
      </div>

      <div className="report-summary">
        {metrics.map((item) => (
          <div key={item.label} className="report-summary-card">
            <span>{item.label}</span>
            <strong>{item.value}</strong>
            <small>{item.caption}</small>
          </div>
        ))}
      </div>

      <div className="report-layout">
        <div className="report-list">
          <div className="report-list-head">
            <strong>{projectLabel(activeProject.projectId)} 리포트</strong>
            <small>{ledgerRows.length}개 저장됨</small>
          </div>
          <div className="report-list-scroll">
            {ledgerRows.map((row) => {
              const active = row.day === selectedDay;
              const perfTone = row.performance ? (row.performance.strategy_return_pct >= 0 ? 'up' : 'down') : '';
              const rowIndex = ledgerRows.findIndex((item) => item.day === row.day);
              const previousLedgerRow = rowIndex > 0 ? ledgerRows[rowIndex - 1] : null;
              const levelSummaryRows = buildLevelRows(
                activeProject.projectId,
                row.decision?.core_data || {},
                previousLedgerRow?.decision?.core_data || {}
              );
              return (
                <button
                  key={`${row.day}-${row.index}`}
                  type="button"
                  className={active ? 'report-item active' : 'report-item'}
                  onClick={() => setSelectedDay(row.day)}
                >
                  <div className="report-item-top">
                    <strong>{row.day}</strong>
                    <span className={`cell-pill cell-${verdictTone(row.verdict)}`}>{verdictLabel(row.verdict)}</span>
                  </div>
                  <div className="report-item-meta">
                    <span>{cleanText(row.tradeMode, '미정')}</span>
                    <span>{cleanText(row.score)}</span>
                    <span>{cleanText(row.confidence)}</span>
                  </div>
                  <div className="report-item-bottom">
                    <span>비중 {formatPercentInt(row.position)}</span>
                    <span className={perfTone}>
                      {row.performance ? `${row.performance.strategy_return_pct >= 0 ? '+' : ''}${row.performance.strategy_return_pct.toFixed(2)}%` : '24h 없음'}
                    </span>
                  </div>
                  {levelSummaryRows.length ? (
                    <div className="report-item-change">
                      {levelSummaryRows.slice(0, 2).map((item) => (
                        <span key={item.key}>{item.label} {item.currentText}</span>
                      ))}
                    </div>
                  ) : null}
                  {row.performance ? (
                    <div className="report-item-pnl">
                      <span>1천만 원 기준</span>
                      <strong className={row.performance.strategy_return_pct >= 0 ? 'up' : 'down'}>
                        {formatNetPnl(pnlFromReturnPct(row.performance.strategy_return_pct, analysisCapital)).amount}
                      </strong>
                    </div>
                  ) : null}
                </button>
              );
            })}
          </div>
        </div>

        <div className="report-detail">
          {selectedRow ? (
            <>
              <div className="report-detail-head">
                <div>
                  <div className="eyebrow">{dayOnly(selectedRow.report.generated_at)}</div>
                  <h4>{verdictLabel(selectedRow.verdict)} · {cleanText(selectedRow.tradeMode, '미정')}</h4>
                  <p className="subtle">
                    점수 {cleanText(selectedRow.score)} · 신뢰도 {cleanText(selectedRow.confidence)} · 보정 표본 {cleanText(selectedRow.sampleCount)}
                  </p>
                </div>
                <div className={`pill pill-${verdictTone(selectedRow.verdict)}`}>{formatPercentInt(selectedRow.position)}</div>
              </div>

              <div className="detail-grid">
                <div className="detail-card">
                  <span>행동</span>
                  <strong>{cleanText(selectedRow.decision?.ai_signal?.action || selectedRow.decision?.decision_brief?.[0], '미정')}</strong>
                  <small>{cleanText(selectedRow.decision?.engine?.entry_condition || selectedRow.decision?.decision_brief?.[1], '진입 조건 없음')}</small>
                </div>
                <div className="detail-card">
                  <span>손익</span>
                  <strong className={performanceTone}>
                    {perf ? `${pct(perf.strategy_return_pct, 2)} · ${reportPnl?.amount || '-'}` : '-'}
                  </strong>
                  <small>{perf ? `벤치마크 ${pct(perf.benchmark_return_pct, 2)} · ${benchmarkPnl?.amount || '-'}` : '24h 성과 없음'}</small>
                </div>
                <div className="detail-card">
                  <span>가격 창</span>
                  <strong>{perf ? dayOnly(perf.generated_at) : '-'}</strong>
                  <small>{perf ? dayOnly(perf.future_generated_at) : '-'}</small>
                </div>
                <div className="detail-card">
                  <span>리포트 타입</span>
                  <strong>{selectedRow.report.record_type || 'report'}</strong>
                  <small>{cleanText(selectedRow.report.record_key)}</small>
                </div>
              </div>

              <div className="analysis-blocks">
                <div className="analysis-block">
                  <h5>핵심 지표</h5>
                  <div className="chip-grid">
                    {coreData.map((item) => (
                      <span key={item.key} className="analysis-chip">
                        <strong>{projectCoreLabel(activeProject.projectId, item.key)}</strong>
                        <small>{formatCoreValue(item.key, item.value)}</small>
                      </span>
                    ))}
                  </div>
                </div>

                <div className="analysis-block">
                  <h5>지표 레벨</h5>
                  {levelRows.length ? (
                    <div className="report-change-grid">
                      {levelRows.map((item) => (
                        <div key={item.key} className="report-change-card">
                          <span>{item.label}</span>
                          <strong>{item.previousText} → {item.currentText}</strong>
                          <small>전일값 → 당일값</small>
                        </div>
                      ))}
                    </div>
                  ) : (
                    <p className="note">지표 레벨 비교 데이터가 없습니다.</p>
                  )}
                </div>

                <div className="analysis-block">
                  <h5>긍정 / 부정</h5>
                  <div className="two-column-notes">
                    <div>
                      <span>긍정</span>
                      <ul>
                        {(positives.length ? positives : ['-']).map((item) => <li key={item}>{item}</li>)}
                      </ul>
                    </div>
                    <div>
                      <span>부정</span>
                      <ul>
                        {(negatives.length ? negatives : ['-']).map((item) => <li key={item}>{item}</li>)}
                      </ul>
                    </div>
                  </div>
                </div>

                <div className="analysis-block">
                  <h5>결정 근거</h5>
                  <ul>
                    {(evidence.length ? evidence : ['-']).map((item) => <li key={item}>{item}</li>)}
                  </ul>
                </div>

                <div className="analysis-block">
                  <h5>Decision brief</h5>
                  <ul>
                    {(brief.length ? brief : ['-']).map((item) => <li key={item}>{item}</li>)}
                  </ul>
                </div>

                <div className="analysis-block">
                  <h5>보정 가드</h5>
                  <div className="guard-grid">
                    <div className="guard-card">
                      <span>보정 판정</span>
                      <strong>{tunedProjectVerdict}</strong>
                      <small>최근 score와 hysteresis 반영</small>
                    </div>
                    <div className="guard-card">
                      <span>권장 모드</span>
                      <strong>{projectGuard.max_trade_mode || '미정'}</strong>
                      <small>cap x{toNumber(projectGuard.position_multiplier_cap, 1).toFixed(2)}</small>
                    </div>
                    <div className="guard-card">
                      <span>비용가중</span>
                      <strong>{projectPerf.weightedAccuracyText || '-'}</strong>
                      <small>Exact {projectPerf.exactAccuracyText || '-'}</small>
                    </div>
                  </div>
                </div>
              </div>

              <div className="report-body-grid">
                <div className="report-body">
                  <h5>리포트 본문</h5>
                  <pre>{reportText}</pre>
                </div>
                <div className="report-body">
                  <h5>24h 성과 해석</h5>
                  <div className="report-metrics">
                    <div>
                      <span>전략 수익률</span>
                      <strong className={perf?.strategy_return_pct >= 0 ? 'up' : 'down'}>{perf ? pct(perf.strategy_return_pct, 2) : '-'}</strong>
                      <small>{reportPnl?.amount || '-'}</small>
                    </div>
                    <div>
                      <span>벤치마크 수익률</span>
                      <strong className={perf?.benchmark_return_pct >= 0 ? 'up' : 'down'}>{perf ? pct(perf.benchmark_return_pct, 2) : '-'}</strong>
                      <small>{benchmarkPnl?.amount || '-'}</small>
                    </div>
                    <div>
                      <span>노출</span>
                      <strong>{perf ? pct(perf.exposure * 100, 0) : '-'}</strong>
                    </div>
                    <div>
                      <span>기준 금액</span>
                      <strong>{currency(analysisCapital)}</strong>
                      <small>순손익 환산 기준</small>
                    </div>
                  </div>
                  {levelRows.length ? (
                    <div className="report-change-grid">
                      {levelRows.slice(0, 4).map((item) => (
                        <div key={item.key} className="report-change-card">
                          <span>{item.label}</span>
                          <strong>{item.previousText} → {item.currentText}</strong>
                          <small>전일값 → 당일값</small>
                        </div>
                      ))}
                    </div>
                  ) : null}
                  <p className="note">
                    {perf
                      ? `${cleanText(perf.asset_name, '대상 자산')}의 실제 지표 레벨 변화와 24h 레그 수익률, 노출, 거래비용을 함께 본다. ${cleanText(perf.regime, 'regime 없음')}`
                      : '이 리포트는 24시간 매칭 결과가 없어 성과 계산이 비어 있다.'}
                  </p>
                  {perf ? (
                    <ul className="perf-detail-list">
                      <li>시작: {dayOnly(perf.generated_at)} → 종료: {dayOnly(perf.future_generated_at)}</li>
                      <li>거래비용: {pct(perf.trade_cost_pct, 3)} · 턴오버: {pct(perf.turnover_pct, 2)}</li>
                      <li>기준 자산: {cleanText(perf.asset_name)}</li>
                      <li>1천만 원 기준 순손익: {reportPnl?.amount || '-'}</li>
                      <li>항상투자: {pct(perf.always_invest_return_pct, 2)} · 현금대비: {pct(perf.always_cash_return_pct, 2)}</li>
                    </ul>
                  ) : null}
                </div>
              </div>
            </>
          ) : (
            <div className="empty-report">리포트가 없습니다.</div>
          )}
        </div>
      </div>

      <div className="report-foot">
        <span>{projectLabel(activeProject.projectId)} / {ledgerRows.length}개 리포트</span>
        <span>백테스트: {projectPerf.strategyReturn || '-'}</span>
        <span>벤치마크: {projectPerf.benchmarkReturn || '-'}</span>
      </div>
    </section>
  );
}

function getPeriodRows(rows, scenarioDays) {
  if (!rows.length) return [];
  const limit = Math.max(1, Math.min(rows.length, Math.round(toNumber(scenarioDays, rows.length))));
  return rows.slice(-limit);
}

function buildPnlTimeline(rows, capital, marketWeight, cryptoWeight) {
  const totalExposure = marketWeight + cryptoWeight;
  const cashWeight = totalExposure >= 1 ? 0 : 1 - totalExposure;
  const normalizedMarketWeight = totalExposure > 1 ? marketWeight / totalExposure : marketWeight;
  const normalizedCryptoWeight = totalExposure > 1 ? cryptoWeight / totalExposure : cryptoWeight;

  let runningPnl = 0;

  return rows.map((row) => {
    const marketReturn = toNumber(row.market?.strategy_return_pct, 0);
    const cryptoReturn = toNumber(row.crypto?.strategy_return_pct, 0);
    const blendedReturn = normalizedMarketWeight * marketReturn + normalizedCryptoWeight * cryptoReturn;
    const netReturn = cashWeight * 0 + blendedReturn;
    const netPnl = capital * (netReturn / 100);
    runningPnl += netPnl;
    const runningCapital = capital + runningPnl;

    return {
      day: row.day,
      marketReturn,
      cryptoReturn,
      blendedReturn,
      netPnl,
      cumulativePnl: runningPnl,
      runningCapital,
      netReturn,
    };
  });
}

function formatNetPnl(value) {
  const number = toNumber(value, 0);
  if (Math.abs(number) < 1) {
    return { label: '순수익', amount: `+${currency(0)}`, tone: 'neutral' };
  }
  return number >= 0
    ? { label: '순수익', amount: `+${currency(number)}`, tone: 'good' }
    : { label: '순손실', amount: `-${currency(Math.abs(number))}`, tone: 'danger' };
}

function pnlFromReturnPct(returnPct, capital = 10000000) {
  return toNumber(capital, 0) * (toNumber(returnPct, 0) / 100);
}

function getScale(values) {
  const min = Math.min(...values);
  const max = Math.max(...values);
  const padding = (max - min || 1) * 0.08;
  return {
    min: min - padding,
    max: max + padding,
  };
}

function buildChartPoints(values, width, height, padding, min, max) {
  const usableWidth = width - padding * 2;
  const usableHeight = height - padding * 2;
  const range = max - min || 1;
  return values.map((value, index) => {
    const x = padding + (usableWidth * index) / Math.max(values.length - 1, 1);
    const y = padding + usableHeight - ((value - min) / range) * usableHeight;
    return { x, y, value };
  });
}

function buildLinePath(points) {
  if (!points.length) return '';
  if (points.length === 1) return `M ${points[0].x} ${points[0].y}`;
  return points
    .map((point, index) => `${index === 0 ? 'M' : 'L'} ${point.x} ${point.y}`)
    .join(' ');
}

function buildAreaPath(points, height, padding) {
  if (!points.length) return '';
  const baseY = height - padding;
  const line = buildLinePath(points);
  const last = points[points.length - 1];
  const first = points[0];
  return `${line} L ${last.x} ${baseY} L ${first.x} ${baseY} Z`;
}

function getTickValues(min, max, count = 4) {
  const step = (max - min) / count || 1;
  return Array.from({ length: count + 1 }, (_, index) => min + step * index);
}

function FancyChart({
  title,
  subtitle,
  series,
  formatValue = (value) => String(value),
  formatValueRight = null,
  compact = false,
  dualAxis = false,
  leftLabel = '',
  rightLabel = '',
}) {
  const width = compact ? 340 : 720;
  const height = compact ? 220 : 260;
  const padding = 24;
  const labels = series[0]?.labels || [];
  const leftSeries = series.filter((item) => item.axis !== 'right');
  const rightSeries = series.filter((item) => item.axis === 'right');
  const leftValues = leftSeries.flatMap((item) => item.values);
  const rightValues = rightSeries.flatMap((item) => item.values);
  const leftScale = getScale(leftValues.length ? leftValues : [0]);
  const rightScale = getScale(rightValues.length ? rightValues : leftValues.length ? leftValues : [0]);
  const ticks = getTickValues(leftScale.min, leftScale.max, 4);

  const geometryFor = (item) => {
    const scale = item.axis === 'right' ? rightScale : leftScale;
    const points = buildChartPoints(item.values, width, height, padding, scale.min, scale.max);
    return {
      points,
      path: buildLinePath(points),
      area: buildAreaPath(points, height, padding),
      scale,
    };
  };

  return (
    <div className="chart-card">
      <div className="chart-head">
        <div>
          <h4>{title}</h4>
          {subtitle ? <p>{subtitle}</p> : null}
        </div>
        <div className="chart-legend">
          {series.map((item) => (
            <span key={item.name}>
              <i style={{ background: item.color }} />
              {item.name}
            </span>
          ))}
        </div>
      </div>
      <svg viewBox={`0 0 ${width} ${height}`} role="img" aria-label={title} className="line-chart">
        <defs>
          {series.map((item) => {
            const fillId = `fill-${slugId(title)}-${slugId(item.name)}`;
            return (
              <linearGradient key={item.name} id={fillId} x1="0" x2="0" y1="0" y2="1">
                <stop offset="0%" stopColor={item.color} stopOpacity={item.areaOpacity ?? 0.14} />
                <stop offset="100%" stopColor={item.color} stopOpacity={0} />
              </linearGradient>
            );
          })}
        </defs>
        {ticks.map((tickValue, index) => {
          const y = padding + ((height - padding * 2) * (ticks.length - 1 - index)) / (ticks.length - 1);
          const leftText = formatValue(tickValue);
          const rightText = dualAxis && rightSeries.length
            ? formatValueRight
              ? formatValueRight(rightScale.min + ((rightScale.max - rightScale.min) * (ticks.length - 1 - index)) / (ticks.length - 1))
              : formatValue(rightScale.min + ((rightScale.max - rightScale.min) * (ticks.length - 1 - index)) / (ticks.length - 1))
            : null;
          return (
            <g key={tickValue}>
              <line x1={padding} x2={width - padding} y1={y} y2={y} className="chart-line-grid" />
              <text x={8} y={y + 3} className="chart-rail chart-axis-left">
                {leftText}
              </text>
              {rightText ? (
                <text x={width - 8} y={y + 3} className="chart-rail chart-axis-right">
                  {rightText}
                </text>
              ) : null}
            </g>
          );
        })}
        {series.map((item) => {
          const geom = geometryFor(item);
          const fillId = `fill-${slugId(title)}-${slugId(item.name)}`;
          const lastPoint = geom.points[geom.points.length - 1];
          return (
            <g key={item.name}>
              {item.area !== false ? <path d={geom.area} fill={`url(#${fillId})`} stroke="none" /> : null}
              <path
                d={geom.path}
                fill="none"
                stroke={item.color}
                strokeWidth={item.strokeWidth || 1.45}
                strokeLinejoin="round"
                strokeLinecap="round"
              />
              {lastPoint ? <circle cx={lastPoint.x} cy={lastPoint.y} r="2.6" fill={item.color} stroke="#08111d" strokeWidth="1.5" /> : null}
            </g>
          );
        })}
        {labels.map((label, index) => {
          const x = padding + ((width - padding * 2) * index) / Math.max(labels.length - 1, 1);
          return (
            <text key={label} x={x} y={height - 7} className="chart-label" textAnchor="middle">
              {label}
            </text>
          );
        })}
      </svg>
    </div>
  );
}

function MetricCard({ label, value, caption, tone = 'neutral' }) {
  return (
    <div className={`metric-card metric-${tone}`}>
      <span>{label}</span>
      <strong>{value}</strong>
      {caption ? <small>{caption}</small> : null}
    </div>
  );
}

function ProjectCard({ project }) {
  const decision = latestDecision(project);
  const report = latestReport(project);
  const perf = performanceSummary(project);
  const calibrationNote = calibrationWarning(project);
  const verdict = verdictLabel(decision?.dashboard?.verdict || decision?.ai_signal?.verdict);
  const tone = verdictTone(verdict);
  const recommendedGuard = perf?.recommendedGuard || {};
  const tunedVerdict = verdictLabel(perf?.hysteresisVerdict || perf?.tunedVerdict);

  return (
    <section className={`panel panel-${tone}`}>
      <div className="panel-head">
        <div>
          <div className="eyebrow">{projectLabel(project.projectId)}</div>
          <h2>{verdict}</h2>
          <p className="subtle">
            {dayOnly(report?.generated_at || decision?.generated_at)} 기준 · 점수 {cleanText(decision?.dashboard?.score ?? decision?.ai_signal?.rule_score)} · 신뢰도 {cleanText(decision?.engine?.confidence_score ?? decision?.ai_signal?.ai_confidence)}
          </p>
        </div>
        <div className={`pill pill-${tone}`}>{formatPercentInt(decision?.engine?.position_size ?? decision?.ai_signal?.position_size)}</div>
      </div>

      <div className="metric-grid">
        <MetricCard label="최근 판정" value={verdict} caption={`보정 ${tunedVerdict}`} tone={tone} />
        <MetricCard label="정확도" value={perf?.exactAccuracyText || '-'} caption={`비용가중 ${perf?.weightedAccuracyText || '-'}`} />
        <MetricCard label="오분류" value={`${perf?.mildMissRateText || '-'} / ${perf?.strongMissRateText || '-'}`} caption={`세부신호 ${perf?.componentHitRateText || '-'}`} />
        <MetricCard label="컷오프" value={perf?.thresholdText || '-'} caption={`${recommendedGuard.max_trade_mode || '미정'} · cap x${toNumber(recommendedGuard.position_multiplier_cap, 1).toFixed(2)}`} />
      </div>

      {calibrationNote ? (
        <div className="mini-block">
          <h3>보정 상태</h3>
          <p className="note">{calibrationNote}</p>
        </div>
      ) : null}

      <div className="mini-block">
        <h3>핵심 근거</h3>
        <ul>
          {(decision?.core_evidence || []).slice(0, 3).map((item) => (
            <li key={item}>{item}</li>
          ))}
        </ul>
      </div>

      <div className="mini-block">
        <h3>한줄 대응</h3>
        <p className="note">
          {cleanText(decision?.ai_signal?.action || decision?.decision_brief?.[1] || decision?.decision_brief?.[0], '미정')}
        </p>
      </div>

      {perf?.benchmarkWeightsLabel ? (
        <div className="mini-block">
          <h3>KRW 바스켓</h3>
          <p className="note">
            {perf.benchmarkWeightsLabel}
            {perf.benchmarkMethod ? ` · ${perf.benchmarkMethod}` : ''}
          </p>
        </div>
      ) : null}

      {perf ? (
        <div className="mini-block">
          <h3>비용 행렬</h3>
          <div className="confusion-grid">
            {VERDICT_ORDER.map((predicted) => (
              <div key={predicted} className="confusion-card">
                <span>{predicted}</span>
                <strong>
                  {VERDICT_ORDER.map((actual) => perf.confusion?.[predicted]?.[actual] ?? 0).join(' · ')}
                </strong>
                <small>중립 · 주의 · 위험 순</small>
              </div>
            ))}
          </div>
        </div>
      ) : null}

      {perf ? (
        <div className="perf-strip">
          <span>Exact {perf.exactAccuracyText || '-'}</span>
          <span>Weighted {perf.weightedAccuracyText || '-'}</span>
          <span>{perf.strategyReturn}</span>
          <span>{perf.benchmarkReturn}</span>
        </div>
      ) : null}
    </section>
  );
}

function DataTable({ rows, startDate, endDate, onStartDate, onEndDate }) {
  return (
    <section className="panel table-panel">
      <div className="section-head">
        <div>
          <div className="eyebrow">Decision table</div>
          <h3>날짜별 점수와 신뢰도</h3>
          <p className="subtle">기간을 좁혀서 볼 수 있고, 목록은 기본적으로 전체 이력을 표시합니다.</p>
        </div>
        <div className="date-filters">
          <label>
            <span>From</span>
            <input type="date" value={startDate} onChange={(e) => onStartDate(e.target.value)} />
          </label>
          <label>
            <span>To</span>
            <input type="date" value={endDate} onChange={(e) => onEndDate(e.target.value)} />
          </label>
        </div>
      </div>

      <div className="table-wrap">
        <table>
          <thead>
            <tr>
              <th>날짜</th>
              <th>마켓 판정</th>
              <th>점수</th>
              <th>신뢰도</th>
              <th>크립토 판정</th>
              <th>점수</th>
              <th>신뢰도</th>
            </tr>
          </thead>
          <tbody>
            {rows.length > 0 ? (
              rows.map((row) => (
                <tr key={row.day}>
                  <td className="date-cell">{row.day}</td>
                  <td>
                    <span className={`cell-pill cell-${verdictTone(row.market?.verdict)}`}>
                      {verdictLabel(row.market?.verdict)}
                    </span>
                  </td>
                  <td className="num-cell">{cleanText(row.market?.score)}</td>
                  <td className="num-cell">{cleanText(row.market?.confidence)}</td>
                  <td>
                    <span className={`cell-pill cell-${verdictTone(row.crypto?.verdict)}`}>
                      {verdictLabel(row.crypto?.verdict)}
                    </span>
                  </td>
                  <td className="num-cell">{cleanText(row.crypto?.score)}</td>
                  <td className="num-cell">{cleanText(row.crypto?.confidence)}</td>
                </tr>
              ))
            ) : (
              <tr>
                <td colSpan="7" className="empty-cell">
                  선택한 기간에 데이터가 없습니다.
                </td>
              </tr>
            )}
          </tbody>
        </table>
      </div>
    </section>
  );
}

function Simulator({ marketProject, cryptoProject }) {
  const [capital, setCapital] = React.useState(10000000);
  const marketDecision = latestDecision(marketProject) || {};
  const cryptoDecision = latestDecision(cryptoProject) || {};
  const marketPerf = performanceSummary(marketProject) || {};
  const cryptoPerf = performanceSummary(cryptoProject) || {};
  const marketReturnRows = getReturnRows(marketProject);
  const cryptoReturnRows = getReturnRows(cryptoProject);
  const combinedReturnRows = mergeReturnRows(marketProject, cryptoProject);
  const marketBasketLabel = marketPerf?.benchmarkWeightsLabel || 'S&P 500 / KOSPI';
  const cryptoBasketLabel = cryptoPerf?.benchmarkWeightsLabel || 'BTC / ETH';

  const [marketWeight, setMarketWeight] = React.useState(Math.round(toNumber(marketDecision.engine?.position_size, 55)));
  const [cryptoWeight, setCryptoWeight] = React.useState(Math.round(toNumber(cryptoDecision.engine?.position_size, 0)));
  const [scenarioDays, setScenarioDays] = React.useState(14);
  const [selectedAssets, setSelectedAssets] = React.useState({
    market: true,
    crypto: true,
  });

  const marketExposure = Math.max(0, Math.min(100, marketWeight)) / 100;
  const cryptoExposure = Math.max(0, Math.min(100, cryptoWeight)) / 100;
  const totalExposure = marketExposure + cryptoExposure;
  const cashExposure = totalExposure >= 1 ? 0 : 1 - totalExposure;
  const normalizedMarketWeight = totalExposure > 1 ? marketExposure / totalExposure : marketExposure;
  const normalizedCryptoWeight = totalExposure > 1 ? cryptoExposure / totalExposure : cryptoExposure;
  const effectiveMarketExposure = selectedAssets.market ? marketExposure : 0;
  const effectiveCryptoExposure = selectedAssets.crypto ? cryptoExposure : 0;
  const effectiveTotalExposure = effectiveMarketExposure + effectiveCryptoExposure;
  const effectiveCashExposure = effectiveTotalExposure >= 1 ? 0 : 1 - effectiveTotalExposure;
  const effectiveNormalizedMarketWeight = effectiveTotalExposure > 1 ? effectiveMarketExposure / effectiveTotalExposure : effectiveMarketExposure;
  const effectiveNormalizedCryptoWeight = effectiveTotalExposure > 1 ? effectiveCryptoExposure / effectiveTotalExposure : effectiveCryptoExposure;
  const activeRows = getPeriodRows(combinedReturnRows, scenarioDays);
  const marketRowsInPeriod = getPeriodRows(marketReturnRows, scenarioDays);
  const cryptoRowsInPeriod = getPeriodRows(cryptoReturnRows, scenarioDays);
  const marketTimeline = buildPnlTimeline(activeRows.map((row) => ({
    day: row.day,
    market: row.market,
    crypto: null,
  })), capital, effectiveMarketExposure, 0);
  const cryptoTimeline = buildPnlTimeline(activeRows.map((row) => ({
    day: row.day,
    market: null,
    crypto: row.crypto,
  })), capital, 0, effectiveCryptoExposure);
  const portfolioTimeline = buildPnlTimeline(activeRows, capital, effectiveNormalizedMarketWeight, effectiveNormalizedCryptoWeight);

  const marketFinal = marketTimeline.at(-1)?.runningCapital ?? capital;
  const cryptoFinal = cryptoTimeline.at(-1)?.runningCapital ?? capital;
  const portfolioFinal = portfolioTimeline.at(-1)?.runningCapital ?? capital;
  const marketPeriodReturnPct = compoundReturnPct(marketFinal, capital);
  const cryptoPeriodReturnPct = compoundReturnPct(cryptoFinal, capital);
  const portfolioPeriodReturnPct = compoundReturnPct(portfolioFinal, capital);

  const marketResult = formatNetPnl(marketFinal - capital);
  const cryptoResult = formatNetPnl(cryptoFinal - capital);
  const blendedResult = formatNetPnl(portfolioFinal - capital);
  const marketDailyResult = formatNetPnl(marketTimeline.at(-1)?.netPnl ?? 0);
  const cryptoDailyResult = formatNetPnl(cryptoTimeline.at(-1)?.netPnl ?? 0);
  const blendedDailyResult = formatNetPnl(portfolioTimeline.at(-1)?.netPnl ?? 0);

  const presets = [7, 14, 30, 90];
  const projectionLabels = activeRows.map((row) => row.day.slice(5));
  const marketPath = marketTimeline.map((point) => point.runningCapital);
  const cryptoPath = cryptoTimeline.map((point) => point.runningCapital);
  const blendedPath = portfolioTimeline.map((point) => point.runningCapital);
  const cashPath = activeRows.map(() => capital * effectiveCashExposure);

  const marketPeriodRows = marketRowsInPeriod.map((row) => ({
    day: dayOnly(row.generated_at),
    market: row,
    crypto: null,
  }));
  const cryptoPeriodRows = cryptoRowsInPeriod.map((row) => ({
    day: dayOnly(row.generated_at),
    market: null,
    crypto: row,
  }));
  const marketPeriodTimeline = buildPnlTimeline(marketPeriodRows, capital, marketExposure, 0);
  const cryptoPeriodTimeline = buildPnlTimeline(cryptoPeriodRows, capital, 0, cryptoExposure);
  const blendedPeriodTimeline = buildPnlTimeline(activeRows, capital, effectiveNormalizedMarketWeight, effectiveNormalizedCryptoWeight);

  return (
    <section className="panel simulator">
      <div className="section-head">
        <div>
          <div className="eyebrow">Capital simulator</div>
          <h3>실투입 예상 수익률</h3>
          <p className="subtle">실제 사후검증 수익률을 날짜별로 누적해서, 리포트대로 투자했을 때의 순손익을 보여줍니다.</p>
        </div>
        <span className="subtle">기준 자산: Market = {marketBasketLabel}, Crypto = {cryptoBasketLabel}</span>
      </div>

      <div className="sim-grid">
        <label className="field">
          <span>투입 금액</span>
          <input
            type="number"
            min="0"
            step="100000"
            value={capital}
            onChange={(e) => setCapital(toNumber(e.target.value, 0))}
          />
        </label>

        <label className="field">
          <span>마켓 비중</span>
          <input
            type="range"
            min="0"
            max="100"
            value={marketWeight}
            onChange={(e) => setMarketWeight(toNumber(e.target.value, 55))}
          />
          <strong>{marketWeight}%</strong>
        </label>

        <label className="field">
          <span>크립토 비중</span>
          <input
            type="range"
            min="0"
            max="100"
            value={cryptoWeight}
            onChange={(e) => setCryptoWeight(toNumber(e.target.value, 0))}
          />
          <strong>{cryptoWeight}%</strong>
        </label>

        <label className="field">
          <span>시나리오 기간(일)</span>
          <input
            type="range"
            min="1"
            max="180"
            value={scenarioDays}
            onChange={(e) => setScenarioDays(toNumber(e.target.value, 14))}
          />
          <strong>{scenarioDays}일</strong>
        </label>
      </div>

      <div className="preset-row">
        {presets.map((days) => (
          <button key={days} type="button" className={scenarioDays === days ? 'preset active' : 'preset'} onClick={() => setScenarioDays(days)}>
            {days}일
          </button>
        ))}
      </div>

      <div className="asset-select">
        <label>
          <input
            type="checkbox"
            checked={selectedAssets.market}
            onChange={(e) => setSelectedAssets((prev) => ({ ...prev, market: e.target.checked }))}
          />
          Market
        </label>
        <label>
          <input
            type="checkbox"
            checked={selectedAssets.crypto}
            onChange={(e) => setSelectedAssets((prev) => ({ ...prev, crypto: e.target.checked }))}
          />
          Crypto
        </label>
      </div>

      <div className="projection-grid">
        <div className="projection-card">
          <span>Market allocation</span>
          <strong className={marketResult.tone === 'danger' ? 'down' : 'up'}>{marketResult.label} {marketResult.amount}</strong>
          <small>{pct(marketPeriodReturnPct, 2)} · 최종 {compactCurrency(marketFinal)}</small>
          <small>선택 기간의 일별 손익 합계</small>
          <small>마지막 샘플: {marketDailyResult.label} {marketDailyResult.amount}</small>
        </div>
        <div className="projection-card">
          <span>Crypto allocation</span>
          <strong className={cryptoResult.tone === 'danger' ? 'down' : 'up'}>{cryptoResult.label} {cryptoResult.amount}</strong>
          <small>{pct(cryptoPeriodReturnPct, 2)} · 최종 {compactCurrency(cryptoFinal)}</small>
          <small>선택 기간의 일별 손익 합계</small>
          <small>마지막 샘플: {cryptoDailyResult.label} {cryptoDailyResult.amount}</small>
        </div>
        <div className="projection-card accent">
          <span>Portfolio</span>
          <strong className={blendedResult.tone === 'danger' ? 'down' : 'up'}>{blendedResult.label} {blendedResult.amount}</strong>
          <small>{pct(portfolioPeriodReturnPct, 2)} · 최종 {compactCurrency(portfolioFinal)}</small>
          <small>기준: 시장 {Math.round(effectiveNormalizedMarketWeight * 100)}% + 크립토 {Math.round(effectiveNormalizedCryptoWeight * 100)}% + 현금 {Math.round(effectiveCashExposure * 100)}%</small>
          <small>선택 기간의 일별 손익 합계</small>
          <small>마지막 샘플: {blendedDailyResult.label} {blendedDailyResult.amount}</small>
        </div>
      </div>

      <div className="calc-box">
        <div>
          <span>계산식</span>
          <strong>리포트별 24h 순손익을 단순 합산합니다.</strong>
        </div>
        <div>
          <span>Market</span>
          <strong>{pct(marketPeriodReturnPct, 2)} · 24h 기준</strong>
          <small>{marketReturnRows.length}개 샘플 · {marketDecision?.engine?.position_size ?? '-'}% 권장</small>
        </div>
        <div>
          <span>Crypto</span>
          <strong>{pct(cryptoPeriodReturnPct, 2)} · 24h 기준</strong>
          <small>{cryptoReturnRows.length}개 샘플 · {cryptoDecision?.engine?.position_size ?? '-'}% 권장</small>
        </div>
        <div>
          <span>Blended</span>
          <strong>{pct(portfolioPeriodReturnPct, 2)} · 24h 기준 · {activeRows.length}개 샘플</strong>
          <small>현금 포함 후 24시간 간격의 순손익을 더합니다.</small>
        </div>
        <div>
          <span>Cash</span>
          <strong>{Math.round(effectiveCashExposure * 100)}%</strong>
          <small>수익률 0%</small>
        </div>
      </div>

      <div className="chart-stack">
        <FancyChart
          title="리포트별 누적 손익"
          subtitle="선택한 기간의 24h 사후검증 손익을 단순 합산한 순손익 경로"
          series={[
            {
              name: 'Cash',
              color: '#7e8aa6',
              values: cashPath,
              labels: projectionLabels,
              axis: 'left',
              area: false,
              strokeWidth: 1.1,
            },
            ...(selectedAssets.market
              ? [{
                  name: 'Market strategy',
                  color: '#52d6a6',
                  values: marketPath,
                  labels: projectionLabels,
                  axis: 'left',
                  area: false,
                  strokeWidth: 1.35,
                }]
              : []),
            ...(selectedAssets.crypto
              ? [{
                  name: 'Crypto strategy',
                  color: '#ffbf63',
                  values: cryptoPath,
                  labels: projectionLabels,
                  axis: 'left',
                  area: false,
                  strokeWidth: 1.35,
                }]
              : []),
            {
              name: 'Blended',
              color: '#ff718f',
              values: blendedPath,
              labels: projectionLabels,
              axis: 'left',
              area: true,
              areaOpacity: 0.12,
              strokeWidth: 1.6,
            },
          ]}
          formatValue={(value) => compactCurrency(value)}
        />
      </div>

      <div className="backtest-grid">
        <div className="backtest-card">
          <span>Market 일별 손익</span>
          <strong>{marketRowsInPeriod.length}개</strong>
          <small>기준 자산: S&P 500 / KOSPI</small>
        </div>
        <div className="backtest-card">
          <span>Crypto 일별 손익</span>
          <strong>{cryptoRowsInPeriod.length}개</strong>
          <small>기준 자산: Bitcoin</small>
        </div>
        <div className="backtest-card">
          <span>Market 기간 누적</span>
          <strong className={marketPeriodReturnPct >= 0 ? 'up' : 'down'}>{pct(marketPeriodReturnPct, 2)}</strong>
          <small>{formatNetPnl(marketFinal - capital).amount}</small>
          <small>기간에 따라 값이 달라져야 정상</small>
        </div>
        <div className="backtest-card">
          <span>Crypto 기간 누적</span>
          <strong className={cryptoPeriodReturnPct >= 0 ? 'up' : 'down'}>{pct(cryptoPeriodReturnPct, 2)}</strong>
          <small>{formatNetPnl(cryptoFinal - capital).amount}</small>
          <small>기간에 따라 값이 달라져야 정상</small>
        </div>
      </div>

      <div className="daily-return-table">
        <div className="daily-return-head">
          <div>
            <h4>일자별 손익과 기간 누적 손익</h4>
            <p>각 날짜의 당일 손익과, 그 시점까지 더한 누적 손익을 나란히 보여줍니다.</p>
          </div>
        </div>
        <table>
          <thead>
            <tr>
              <th>날짜</th>
              <th>Market 일별</th>
              <th>Market 누적</th>
              <th>Crypto 일별</th>
              <th>Crypto 누적</th>
              <th>Portfolio 일별</th>
              <th>Portfolio 누적</th>
            </tr>
          </thead>
          <tbody>
            {activeRows.map((row, index) => {
              const marketPoint = marketTimeline[index];
              const cryptoPoint = cryptoTimeline[index];
              const portfolioPoint = portfolioTimeline[index];
              return (
                <tr key={row.day}>
                  <td>{row.day}</td>
                  <td className={marketPoint?.netPnl >= 0 ? 'up' : 'down'}>
                    {marketPoint ? `${marketPoint.netPnl >= 0 ? '+' : '-'}${currency(Math.abs(marketPoint.netPnl))}` : '-'}
                  </td>
                  <td className={marketPoint?.cumulativePnl >= 0 ? 'up' : 'down'}>
                    {marketPoint ? `${marketPoint.cumulativePnl >= 0 ? '+' : '-'}${currency(Math.abs(marketPoint.cumulativePnl))}` : '-'}
                  </td>
                  <td className={cryptoPoint?.netPnl >= 0 ? 'up' : 'down'}>
                    {cryptoPoint ? `${cryptoPoint.netPnl >= 0 ? '+' : '-'}${currency(Math.abs(cryptoPoint.netPnl))}` : '-'}
                  </td>
                  <td className={cryptoPoint?.cumulativePnl >= 0 ? 'up' : 'down'}>
                    {cryptoPoint ? `${cryptoPoint.cumulativePnl >= 0 ? '+' : '-'}${currency(Math.abs(cryptoPoint.cumulativePnl))}` : '-'}
                  </td>
                  <td className={portfolioPoint?.netPnl >= 0 ? 'up' : 'down'}>
                    {portfolioPoint ? `${portfolioPoint.netPnl >= 0 ? '+' : '-'}${currency(Math.abs(portfolioPoint.netPnl))}` : '-'}
                  </td>
                  <td className={portfolioPoint?.cumulativePnl >= 0 ? 'up' : 'down'}>
                    {portfolioPoint ? `${portfolioPoint.cumulativePnl >= 0 ? '+' : '-'}${currency(Math.abs(portfolioPoint.cumulativePnl))}` : '-'}
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>

      <div className="risk-band">
        <div>
          <span>기간 누적 순손익률</span>
          <strong>{pct(portfolioPeriodReturnPct, 2)} · 24h 기준</strong>
        </div>
        <div>
          <span>마지막 일별 순손익</span>
          <strong>{`${(portfolioTimeline.at(-1)?.netPnl || 0) >= 0 ? '+' : '-'}${currency(Math.abs(portfolioTimeline.at(-1)?.netPnl || 0))}`}</strong>
        </div>
      </div>
    </section>
  );
}

function BacktestSummary({ marketProject, cryptoProject }) {
  const market = performanceSummary(marketProject);
  const crypto = performanceSummary(cryptoProject);

  return (
    <section className="panel backtest-panel">
      <div className="section-head">
        <div>
          <div className="eyebrow">Backtest</div>
          <h3>사후 검증</h3>
          <p className="subtle">이건 실제 매매내역이 아니라, 지난 리포트가 24시간 뒤 실제 결과를 얼마나 잘 맞췄는지 보는 검증입니다.</p>
        </div>
      </div>
      <div className="backtest-grid">
        <div className="backtest-card">
          <span>Market</span>
          <strong>{market?.exactAccuracyText || '-'}</strong>
          <small>비용가중 {market?.weightedAccuracyText || '-'}</small>
          <small>{market?.strategyReturn || '-'}</small>
          <small>최대낙폭 {market?.maxDrawdownText || '-'}</small>
        </div>
        <div className="backtest-card">
          <span>Crypto</span>
          <strong>{crypto?.exactAccuracyText || '-'}</strong>
          <small>비용가중 {crypto?.weightedAccuracyText || '-'}</small>
          <small>{crypto?.strategyReturn || '-'}</small>
          <small>최대낙폭 {crypto?.maxDrawdownText || '-'}</small>
        </div>
      </div>
      <div className="backtest-note">
        <strong>쉽게 말하면</strong>
        <p>
          점수가 좋았던 날이 실제로도 좋았는지, 그리고 그 판단을 따라갔을 때 결과가 어땠는지를 확인하는 기록입니다.
          현재 화면의 실투입 예상 수익률은 각 리포트의 24시간 실제 가격 변화와 사후검증 보정값을 함께 반영해 추정합니다.
        </p>
      </div>
    </section>
  );
}

function App() {
  const [marketProject, cryptoProject] = snapshot.projects;
  const combinedRows = mergeDailyRows(marketProject, cryptoProject);
  const trendScore = buildTrendSeries(combinedRows, 'score');
  const trendConfidence = buildTrendSeries(combinedRows, 'confidence');
  const trendLabels = combinedRows.map((row) => row.day.slice(5));
  const [startDate, setStartDate] = React.useState(combinedRows[0]?.day || '');
  const [endDate, setEndDate] = React.useState(combinedRows[combinedRows.length - 1]?.day || '');

  const filteredRows = combinedRows.filter((row) => {
    if (startDate && row.day < startDate) return false;
    if (endDate && row.day > endDate) return false;
    return true;
  });

  const latestDay = dayOnly(snapshot.generatedAt || marketProject.latestDecision?.generated_at || cryptoProject.latestDecision?.generated_at);

  return (
    <main className="shell">
      <div className="ambient ambient-a" />
      <div className="ambient ambient-b" />

      <header className="hero">
        <div>
          <div className="eyebrow">Live dashboard</div>
          <h1>투자 리포트 모니터</h1>
          <p className="hero-copy">
            마켓과 크립토의 최신 판단을 한 화면에 묶고, 날짜 범위별 점수와 신뢰도를 바로 비교할 수 있게 만든 관제판입니다.
          </p>
        </div>
        <div className="hero-meta">
          <div>
            <span>마지막 갱신</span>
            <strong>{latestDay}</strong>
          </div>
          <div>
            <span>마켓 보고서</span>
            <strong>{cleanText(marketProject.reportCount, 0)}건</strong>
          </div>
          <div>
            <span>크립토 보고서</span>
            <strong>{cleanText(cryptoProject.reportCount, 0)}건</strong>
          </div>
        </div>
      </header>

      <section className="deck">
        <ProjectCard project={marketProject} />
        <ProjectCard project={cryptoProject} />
      </section>

      <section className="panel chart-panel">
        <div className="section-head">
          <div>
            <div className="eyebrow">Trend chart</div>
            <h3>날짜별 점수와 신뢰도</h3>
            <p className="subtle">두 지표를 한 장에 합쳐서 흐름이 바로 보이도록 했습니다.</p>
          </div>
        </div>
        <FancyChart
          title="Trend"
          subtitle="마켓 점수와 신뢰도, 크립토 점수와 신뢰도를 한 장에서 비교"
          series={[
            { name: 'Market score', color: '#52d6a6', values: trendScore.market, labels: trendLabels, axis: 'left', area: true, areaOpacity: 0.1, strokeWidth: 1.8 },
            { name: 'Crypto score', color: '#ffbf63', values: trendScore.crypto, labels: trendLabels, axis: 'left', area: false, strokeWidth: 1.35 },
            { name: 'Market confidence', color: '#8aa8ff', values: trendConfidence.market, labels: trendLabels, axis: 'right', area: true, areaOpacity: 0.08, strokeWidth: 1.55 },
            { name: 'Crypto confidence', color: '#ff718f', values: trendConfidence.crypto, labels: trendLabels, axis: 'right', area: false, strokeWidth: 1.3 },
          ]}
          formatValue={(value) => `${Math.round(value)}`}
          formatValueRight={(value) => `${Math.round(value)}%`}
          dualAxis
        />
      </section>

      <ReportExplorer marketProject={marketProject} cryptoProject={cryptoProject} />

      <DataTable
        rows={filteredRows}
        startDate={startDate}
        endDate={endDate}
        onStartDate={setStartDate}
        onEndDate={setEndDate}
      />

      <section className="workspace">
        <Simulator marketProject={marketProject} cryptoProject={cryptoProject} />
        <BacktestSummary marketProject={marketProject} cryptoProject={cryptoProject} />
      </section>
    </main>
  );
}

export default App;
