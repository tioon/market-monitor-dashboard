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
  if (!text) return null;

  const pick = (label) => {
    const match = text.match(new RegExp(`${label}:\\s*([^\\n]+)`));
    return match ? match[1].trim() : '-';
  };

  return {
    accuracy: pick('판단 적중률'),
    mae: pick('평균 점수 오차'),
    winRate: pick('전략 승률'),
    maxDrawdown: pick('최대 낙폭'),
    strategyReturn: pick('전략 누적 수익률'),
    benchmarkReturn: pick('SPY 누적 수익률') !== '-' ? pick('SPY 누적 수익률') : pick('BTC 누적 수익률'),
  };
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

function projectLabel(projectId) {
  return projectId === 'market-agent' ? 'Market Agent' : 'Crypto Agent';
}

function projectedReturn(baseCapital, marketShare, cryptoShare, marketEdge, cryptoEdge, cycles) {
  const blendedEdge = marketEdge * marketShare + cryptoEdge * cryptoShare;
  return baseCapital * (Math.pow(1 + blendedEdge / 100, cycles) - 1);
}

function formatPnl(value) {
  const number = toNumber(value, 0);
  if (Math.abs(number) < 1) {
    return { label: '보합', amount: currency(0), tone: 'neutral' };
  }
  return number >= 0
    ? { label: '수익', amount: currency(number), tone: 'good' }
    : { label: '손실', amount: currency(Math.abs(number)), tone: 'danger' };
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
        <MetricCard label="기대수익률" value={pct(decision?.calibration?.expected_return_pct ?? 0, 2)} caption="보정 기대값" />
        <MetricCard label="샘플" value={cleanText(decision?.calibration?.sample_count ?? 0)} caption="보정 표본 수" />
        <MetricCard label="품질" value={`${cleanText(decision?.engine?.quality_score ?? '-')}/100`} caption="데이터 품질" />
      </div>

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

      {perf ? (
        <div className="perf-strip">
          <span>{perf.accuracy}</span>
          <span>{perf.strategyReturn}</span>
          <span>{perf.maxDrawdown}</span>
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
              <th>마켓 verdict</th>
              <th>점수</th>
              <th>신뢰도</th>
              <th>크립토 verdict</th>
              <th>점수</th>
              <th>신뢰도</th>
            </tr>
          </thead>
          <tbody>
            {rows.length > 0 ? (
              rows.map((row) => (
                <tr key={row.day}>
                  <td>{row.day}</td>
                  <td>{verdictLabel(row.market?.verdict)}</td>
                  <td>{cleanText(row.market?.score)}</td>
                  <td>{cleanText(row.market?.confidence)}</td>
                  <td>{verdictLabel(row.crypto?.verdict)}</td>
                  <td>{cleanText(row.crypto?.score)}</td>
                  <td>{cleanText(row.crypto?.confidence)}</td>
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
  const [marketWeight, setMarketWeight] = React.useState(50);
  const [cryptoWeight, setCryptoWeight] = React.useState(50);
  const [scenarioDays, setScenarioDays] = React.useState(14);

  const marketDecision = latestDecision(marketProject) || {};
  const cryptoDecision = latestDecision(cryptoProject) || {};

  const marketConfidence = toNumber(marketDecision.engine?.confidence_score, 0) / 100;
  const cryptoConfidence = toNumber(cryptoDecision.engine?.confidence_score, 0) / 100;

  const marketMultiplier = toNumber(marketDecision.calibration?.recommended_position_multiplier, 1);
  const cryptoMultiplier = toNumber(cryptoDecision.calibration?.recommended_position_multiplier, 1);

  const marketEdge = toNumber(marketDecision.calibration?.expected_return_pct, 0) * marketMultiplier * marketConfidence;
  const cryptoEdge = toNumber(cryptoDecision.calibration?.expected_return_pct, 0) * cryptoMultiplier * cryptoConfidence;

  const marketShare = marketWeight / 100;
  const cryptoShare = cryptoWeight / 100;
  const cycleFactor = Math.max(0.25, scenarioDays / 7);

  const marketProjected = projectedReturn(capital, marketShare, 0, marketEdge, 0, cycleFactor);
  const cryptoProjected = projectedReturn(capital, 0, cryptoShare, 0, cryptoEdge, cycleFactor);
  const blendedProjected = projectedReturn(capital, marketShare, cryptoShare, marketEdge, cryptoEdge, cycleFactor);

  const marketResult = formatPnl(marketProjected);
  const cryptoResult = formatPnl(cryptoProjected);
  const blendedResult = formatPnl(blendedProjected);

  const presets = [7, 14, 30, 90];

  return (
    <section className="panel simulator">
      <div className="section-head">
        <div>
          <div className="eyebrow">Capital simulator</div>
          <h3>실투입 예상 수익률</h3>
        </div>
        <span className="subtle">시나리오 기간과 비중을 쉽게 바꿀 수 있게 했습니다.</span>
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
            onChange={(e) => {
              const next = toNumber(e.target.value, 50);
              setMarketWeight(next);
              setCryptoWeight(100 - next);
            }}
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
            onChange={(e) => {
              const next = toNumber(e.target.value, 50);
              setCryptoWeight(next);
              setMarketWeight(100 - next);
            }}
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

      <div className="projection-grid">
        <div className="projection-card">
          <span>Market only</span>
          <strong className={marketResult.tone === 'danger' ? 'down' : 'up'}>{marketResult.label} {marketResult.amount}</strong>
          <small>{pct((marketProjected / Math.max(capital, 1) - 1) * 100, 2)} · 최종 {compactCurrency(marketProjected)}</small>
        </div>
        <div className="projection-card">
          <span>Crypto only</span>
          <strong className={cryptoResult.tone === 'danger' ? 'down' : 'up'}>{cryptoResult.label} {cryptoResult.amount}</strong>
          <small>{pct((cryptoProjected / Math.max(capital, 1) - 1) * 100, 2)} · 최종 {compactCurrency(cryptoProjected)}</small>
        </div>
        <div className="projection-card accent">
          <span>Blended portfolio</span>
          <strong className={blendedResult.tone === 'danger' ? 'down' : 'up'}>{blendedResult.label} {blendedResult.amount}</strong>
          <small>{pct((blendedProjected / Math.max(capital, 1) - 1) * 100, 2)} · 최종 {compactCurrency(blendedProjected)}</small>
        </div>
      </div>

      <div className="risk-band">
        <div>
          <span>보정 기대수익률</span>
          <strong>{pct(marketEdge * marketShare + cryptoEdge * cryptoShare, 2)}</strong>
        </div>
        <div>
          <span>시나리오 끝 예상금액</span>
          <strong>{compactCurrency(blendedProjected)}</strong>
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
    </section>
  );
}

function App() {
  const [marketProject, cryptoProject] = snapshot.projects;
  const combinedRows = mergeDailyRows(marketProject, cryptoProject);
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
