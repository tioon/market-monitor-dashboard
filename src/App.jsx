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

function latestDecision(project) {
  return project.latestDecision?.decision_snapshot || null;
}

function latestReport(project) {
  return project.latestReport || null;
}

function performanceSummary(project) {
  const text = project.performance?.report_text || '';
  const report = project.performance?.report_json || {};
  const returnRows = Array.isArray(report.return_rows) ? report.return_rows : [];
  if (!text) return null;

  const escapeRegExp = (value) => String(value).replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
  const pick = (label) => {
    const match = text.match(new RegExp(`${escapeRegExp(label)}:\\s*([^\\n]+)`));
    return match ? match[1].trim() : '-';
  };

  const firstPick = (...labels) => {
    for (const label of labels) {
      const value = pick(label);
      if (value !== '-') return value;
    }
    return '-';
  };

  const avgReturn = (key) => {
    if (!returnRows.length) return null;
    const total = returnRows.reduce((sum, row) => sum + toNumber(row?.[key], 0), 0);
    return total / returnRows.length;
  };

  const calibration = report.calibration || {};
  const benchmarkWeights = calibration.benchmark_weights || {};
  const isMarket = project.projectId === 'market-agent';
  const leftLabel = isMarket ? 'S&P 500' : 'BTC';
  const rightLabel = isMarket ? 'KOSPI' : 'ETH';
  const leftWeight = toNumber(benchmarkWeights.left_weight, 0.5);
  const rightWeight = toNumber(benchmarkWeights.right_weight, 0.5);

  return {
    accuracy: firstPick('판단 적중률'),
    mae: pick('평균 점수 오차'),
    winRate: firstPick('전략 승률'),
    maxDrawdown: firstPick('최대 낙폭'),
    strategyReturn: firstPick('전략 누적 수익률'),
    benchmarkReturn: firstPick('벤치마크 누적 수익률', 'S&P 500(원화) 누적 수익률', 'KRW 바스켓 누적 수익률'),
    sampleCount: returnRows.length,
    avgStrategyReturnPct: avgReturn('strategy_return_pct'),
    avgBenchmarkReturnPct: avgReturn('benchmark_return_pct') ?? avgReturn('spy_return_pct') ?? avgReturn('kospi_return_pct') ?? avgReturn('asset_return_pct'),
    exposureRate: returnRows.length ? returnRows.reduce((sum, row) => sum + toNumber(row.exposure, 0), 0) / returnRows.length : null,
    benchmarkWeightsLabel: `${leftLabel} ${Math.round(leftWeight * 100)}% / ${rightLabel} ${Math.round(rightWeight * 100)}%`,
    benchmarkMethod: benchmarkWeights.method || '-',
  };
}

function calibrationWarning(project) {
  const decision = latestDecision(project);
  const calibration = decision?.calibration || {};
  const reportRows = Array.isArray(project.performance?.report_json?.return_rows) ? project.performance.report_json.return_rows : [];
  const uniqueScores = new Set(
    reportRows
      .map((row) => row?.predicted_score)
      .filter((value) => value !== null && value !== undefined && value !== '')
  ).size;
  const sampleCount = toNumber(calibration.sample_count ?? reportRows.length, 0);
  const r2 = toNumber(calibration.model?.r2, 0);
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

  if (!issues.length) return null;

  return `최근 보정값은 예측력보다 평균값에 가깝습니다. ${issues.join(' ')}`;
}

function slugId(value) {
  return String(value || '').replace(/[^a-z0-9]+/gi, '-').toLowerCase();
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

  let runningCapital = capital;

  return rows.map((row) => {
    const marketReturn = toNumber(row.market?.strategy_return_pct, 0);
    const cryptoReturn = toNumber(row.crypto?.strategy_return_pct, 0);
    const blendedReturn = normalizedMarketWeight * marketReturn + normalizedCryptoWeight * cryptoReturn;
    const netReturn = cashWeight * 0 + blendedReturn;
    const netPnl = runningCapital * (netReturn / 100);
    runningCapital += netPnl;

    return {
      day: row.day,
      marketReturn,
      cryptoReturn,
      blendedReturn,
      netPnl,
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
        <MetricCard label="권장 비중" value={formatPercentInt(decision?.engine?.position_size)} caption="현재 엔진 기준" tone={tone} />
        <MetricCard label="실투입 손익" value={perf?.strategyReturn || pct(decision?.calibration?.expected_return_pct ?? 0, 2)} caption={perf?.benchmarkReturn ? `벤치마크 ${perf.benchmarkReturn}` : 'KRW 기준'} />
        <MetricCard label="KRW 바스켓" value={perf?.benchmarkWeightsLabel || '-'} caption={perf?.benchmarkMethod || '역사 데이터 기준'} />
        <MetricCard label="샘플" value={cleanText(decision?.calibration?.sample_count ?? 0)} caption="보정 표본 수" />
        <MetricCard label="품질" value={`${cleanText(decision?.engine?.quality_score ?? '-')}/100`} caption="데이터 품질" />
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
        <div className="perf-strip">
          <span>{perf.accuracy}</span>
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
  const activeRows = getPeriodRows(combinedReturnRows, scenarioDays);
  const marketRowsInPeriod = getPeriodRows(marketReturnRows, scenarioDays);
  const cryptoRowsInPeriod = getPeriodRows(cryptoReturnRows, scenarioDays);
  const marketTimeline = buildPnlTimeline(activeRows.map((row) => ({
    day: row.day,
    market: row.market,
    crypto: null,
  })), capital, marketExposure, 0);
  const cryptoTimeline = buildPnlTimeline(activeRows.map((row) => ({
    day: row.day,
    market: null,
    crypto: row.crypto,
  })), capital, 0, cryptoExposure);
  const portfolioTimeline = buildPnlTimeline(activeRows, capital, normalizedMarketWeight, normalizedCryptoWeight);

  const marketFinal = marketTimeline.at(-1)?.runningCapital ?? capital;
  const cryptoFinal = cryptoTimeline.at(-1)?.runningCapital ?? capital;
  const portfolioFinal = portfolioTimeline.at(-1)?.runningCapital ?? capital;

  const marketResult = formatNetPnl(marketFinal - capital);
  const cryptoResult = formatNetPnl(cryptoFinal - capital);
  const blendedResult = formatNetPnl(portfolioFinal - capital);

  const presets = [7, 14, 30, 90];
  const projectionLabels = activeRows.map((row) => row.day.slice(5));
  const marketPath = marketTimeline.map((point) => point.runningCapital);
  const cryptoPath = cryptoTimeline.map((point) => point.runningCapital);
  const blendedPath = portfolioTimeline.map((point) => point.runningCapital);
  const cashPath = activeRows.map(() => capital * cashExposure);
  const marketDailyEdge = marketTimeline.length ? marketTimeline.reduce((sum, point) => sum + point.marketReturn, 0) / marketTimeline.length : 0;
  const cryptoDailyEdge = cryptoTimeline.length ? cryptoTimeline.reduce((sum, point) => sum + point.cryptoReturn, 0) / cryptoTimeline.length : 0;
  const blendedDailyEdge = portfolioTimeline.length ? portfolioTimeline.reduce((sum, point) => sum + point.netReturn, 0) / portfolioTimeline.length : 0;

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
          <small>{pct(((marketFinal / Math.max(capital, 1)) - 1) * 100, 2)} · 최종 {compactCurrency(marketFinal)}</small>
          <small>기준: Market 백테스트 {pct(marketDailyEdge, 2)} / day</small>
        </div>
        <div className="projection-card">
          <span>Crypto allocation</span>
          <strong className={cryptoResult.tone === 'danger' ? 'down' : 'up'}>{cryptoResult.label} {cryptoResult.amount}</strong>
          <small>{pct(((cryptoFinal / Math.max(capital, 1)) - 1) * 100, 2)} · 최종 {compactCurrency(cryptoFinal)}</small>
          <small>기준: Crypto 백테스트 {pct(cryptoDailyEdge, 2)} / day</small>
        </div>
        <div className="projection-card accent">
          <span>Portfolio</span>
          <strong className={blendedResult.tone === 'danger' ? 'down' : 'up'}>{blendedResult.label} {blendedResult.amount}</strong>
          <small>{pct(((portfolioFinal / Math.max(capital, 1)) - 1) * 100, 2)} · 최종 {compactCurrency(portfolioFinal)}</small>
          <small>기준: 시장 {Math.round(normalizedMarketWeight * 100)}% + 크립토 {Math.round(normalizedCryptoWeight * 100)}% + 현금 {Math.round(cashExposure * 100)}%</small>
        </div>
      </div>

      <div className="calc-box">
        <div>
          <span>계산식</span>
          <strong>일별 리포트 순손익을 누적합니다.</strong>
        </div>
        <div>
          <span>Market</span>
          <strong>{pct(marketDailyEdge, 2)} / day</strong>
          <small>{marketReturnRows.length}개 샘플 · {marketDecision?.engine?.position_size ?? '-'}% 권장</small>
        </div>
        <div>
          <span>Crypto</span>
          <strong>{pct(cryptoDailyEdge, 2)} / day</strong>
          <small>{cryptoReturnRows.length}개 샘플 · {cryptoDecision?.engine?.position_size ?? '-'}% 권장</small>
        </div>
        <div>
          <span>Blended</span>
          <strong>{pct(blendedDailyEdge, 2)} / day · {activeRows.length}개 샘플</strong>
          <small>현금 포함 후 일자별 순손익을 누적합니다.</small>
        </div>
        <div>
          <span>Cash</span>
          <strong>{Math.round(cashExposure * 100)}%</strong>
          <small>수익률 0%</small>
        </div>
      </div>

      <div className="chart-stack">
        <FancyChart
          title="리포트별 누적 손익"
          subtitle="날짜별 사후검증 수익률을 누적한 순손익 경로"
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
          <span>Market daily rows</span>
          <strong>{marketRowsInPeriod.length}개</strong>
          <small>기준 자산: S&P 500 / KOSPI</small>
        </div>
        <div className="backtest-card">
          <span>Crypto daily rows</span>
          <strong>{cryptoRowsInPeriod.length}개</strong>
          <small>기준 자산: Bitcoin</small>
        </div>
      </div>

      <div className="daily-return-table">
        <div className="daily-return-head">
          <div>
            <h4>일자별 순손익</h4>
            <p>리포트 날짜별로 실제 백테스트 수익률을 누적한 결과입니다.</p>
          </div>
        </div>
        <table>
          <thead>
            <tr>
              <th>날짜</th>
              <th>Market</th>
              <th>Crypto</th>
              <th>Portfolio</th>
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
                  <td className={cryptoPoint?.netPnl >= 0 ? 'up' : 'down'}>
                    {cryptoPoint ? `${cryptoPoint.netPnl >= 0 ? '+' : '-'}${currency(Math.abs(cryptoPoint.netPnl))}` : '-'}
                  </td>
                  <td className={portfolioPoint?.netPnl >= 0 ? 'up' : 'down'}>
                    {portfolioPoint ? `${portfolioPoint.netPnl >= 0 ? '+' : '-'}${currency(Math.abs(portfolioPoint.netPnl))}` : '-'}
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>

      <div className="risk-band">
        <div>
          <span>현금 포함 순손익률</span>
          <strong>{pct(blendedDailyEdge, 2)} / day</strong>
        </div>
        <div>
          <span>시나리오 끝 순손익</span>
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
          <p className="subtle">이건 실제 매매내역이 아니라, 지난 리포트가 다음 리포트 결과를 얼마나 잘 맞췄는지 보는 검증입니다.</p>
        </div>
      </div>
      <div className="backtest-grid">
        <div className="backtest-card">
          <span>Market</span>
          <strong>{market?.accuracy || '-'}</strong>
          <small>{market?.strategyReturn || '-'}</small>
          <small>{market?.maxDrawdown || '-'}</small>
        </div>
        <div className="backtest-card">
          <span>Crypto</span>
          <strong>{crypto?.accuracy || '-'}</strong>
          <small>{crypto?.strategyReturn || '-'}</small>
          <small>{crypto?.maxDrawdown || '-'}</small>
        </div>
      </div>
      <div className="backtest-note">
        <strong>쉽게 말하면</strong>
        <p>
          점수가 좋았던 날이 실제로도 좋았는지, 그리고 그 판단을 따라갔을 때 결과가 어땠는지를 확인하는 기록입니다.
          현재 화면의 실투입 예상 수익률은 이 사후검증의 보정값을 현재 리포트에 적용해서 추정합니다.
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
