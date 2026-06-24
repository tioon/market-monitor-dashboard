import React from 'react';
import snapshot from './data/snapshot.json';

const verdictTheme = {
  '위험 우위': 'danger',
  '주의': 'caution',
  '중립~우호': 'good',
  '횡보/중립': 'good',
  '방어 우위': 'danger',
  '고변동성/방어': 'danger',
};

function formatDate(value) {
  if (!value) return '-';
  return value.replace('T', ' ').replace('+09:00', '');
}

function toNumber(value, fallback = 0) {
  const parsed = Number(value);
  return Number.isFinite(parsed) ? parsed : fallback;
}

function parsePerformance(project) {
  if (!project?.performance) return null;
  return project.performance;
}

function getLatest(project) {
  return {
    report: project.latestReport || null,
    decision: project.latestDecision || null,
    performance: parsePerformance(project),
  };
}

function clamp(value, min, max) {
  return Math.min(max, Math.max(min, value));
}

function pct(value, digits = 1) {
  const n = toNumber(value, 0);
  return `${n >= 0 ? '+' : ''}${n.toFixed(digits)}%`;
}

function currency(value) {
  return new Intl.NumberFormat('ko-KR', {
    style: 'currency',
    currency: 'KRW',
    maximumFractionDigits: 0,
  }).format(value);
}

function moneyShort(value) {
  return new Intl.NumberFormat('ko-KR', {
    notation: 'compact',
    maximumFractionDigits: 1,
  }).format(value);
}

function verdictClass(verdict) {
  return verdictTheme[verdict] || 'neutral';
}

function getDecisionMetrics(project) {
  const decision = project.latestDecision?.decision_snapshot || {};
  const dashboard = decision.dashboard || {};
  const engine = decision.engine || {};
  const calibration = decision.calibration || {};
  return {
    verdict: dashboard.verdict || '-',
    score: dashboard.score ?? '-',
    confidence: engine.confidence_score ?? '-',
    position: engine.position_size ?? '-',
    lossLimit: engine.daily_loss_limit_pct ?? '-',
    mode: engine.trade_mode || '-',
    quality: engine.quality_score ?? '-',
    expectedReturn: calibration.expected_return_pct ?? 0,
    recommendedMultiplier: calibration.recommended_position_multiplier ?? 1,
    sampleCount: calibration.sample_count ?? 0,
    brief: decision.decision_brief || [],
    evidence: decision.core_evidence || [],
    buffers: decision.buffers || [],
    coreData: decision.core_data || {},
  };
}

function summarizePerformance(performance) {
  if (!performance) return null;
  const reportText = performance.report_text || '';
  const match = (label) => {
    const found = reportText.match(new RegExp(`${label}:\\s*([+\\-0-9.]+%)`));
    return found ? found[1] : null;
  };
  const count = (label) => {
    const found = reportText.match(new RegExp(`${label}:\\s*([0-9/().%\\s]+)`));
    return found ? found[1] : null;
  };

  return {
    accuracy: count('판단 적중률'),
    mae: count('평균 점수 오차'),
    winRate: count('전략 승률'),
    maxDrawdown: count('최대 낙폭'),
    strategyReturn: match('전략 누적 수익률'),
    benchmarkReturn: match('SPY 누적 수익률') || match('BTC 누적 수익률'),
    excessReturn: match('SPY 초과 수익률') || match('초과 수익률'),
  };
}

function extractTimeline(project) {
  const reports = project.reports || [];
  const decisions = project.decisions || [];
  const merged = [];
  const seen = new Set();

  for (const item of [...reports, ...decisions]) {
    const dt = item.generated_at;
    if (!dt || seen.has(dt)) continue;
    seen.add(dt);
    merged.push(item);
  }

  return merged
    .sort((a, b) => (a.generated_at > b.generated_at ? -1 : 1))
    .slice(0, 6);
}

function projectLabel(projectId) {
  return projectId === 'market-agent' ? 'Market Agent' : 'Crypto Agent';
}

function projectedReturn(baseCapital, marketWeight, cryptoWeight, marketEdge, cryptoEdge, cycles) {
  const blendedEdge = marketEdge * marketWeight + cryptoEdge * cryptoWeight;
  const growth = Math.pow(1 + blendedEdge / 100, cycles) - 1;
  return baseCapital * growth;
}

function SignalRow({ label, value, tone, suffix = '' }) {
  return (
    <div className="signal-row">
      <div>
        <div className="signal-label">{label}</div>
        <div className="signal-value">{value}{suffix}</div>
      </div>
      <div className={`signal-pill signal-${tone}`}>{tone}</div>
    </div>
  );
}

function ReportCard({ project }) {
  const latest = getLatest(project);
  const metrics = getDecisionMetrics(project);
  const performance = summarizePerformance(latest.performance);
  const tone = verdictClass(metrics.verdict);
  const reportDate = formatDate(latest.report?.generated_at || latest.decision?.generated_at);

  return (
    <section className={`panel panel-${tone}`}>
      <div className="panel-topline">
        <span className="eyebrow">{projectLabel(project.projectId)}</span>
        <span className="muted">{reportDate}</span>
      </div>
      <div className="panel-header">
        <div>
          <h2>{metrics.verdict}</h2>
          <p className="panel-subtitle">
            점수 {metrics.score} · 신뢰도 {metrics.confidence} · 모드 {metrics.mode}
          </p>
        </div>
        <div className={`badge badge-${tone}`}>Position {metrics.position}%</div>
      </div>

      <div className="metric-grid">
        <div className="metric">
          <span>Expected return</span>
          <strong>{pct(metrics.expectedReturn, 2)}</strong>
        </div>
        <div className="metric">
          <span>Quality</span>
          <strong>{metrics.quality}/100</strong>
        </div>
        <div className="metric">
          <span>Samples</span>
          <strong>{metrics.sampleCount}</strong>
        </div>
        <div className="metric">
          <span>Loss limit</span>
          <strong>{pct(metrics.lossLimit, 2)}</strong>
        </div>
      </div>

      <div className="mini-section">
        <h3>핵심 근거</h3>
        <ul className="bullet-list">
          {metrics.evidence.slice(0, 3).map((item) => (
            <li key={item}>{item}</li>
          ))}
        </ul>
      </div>

      <div className="mini-section">
        <h3>완충 신호</h3>
        <ul className="bullet-list subtle">
          {metrics.buffers.slice(0, 2).map((item) => (
            <li key={item}>{item}</li>
          ))}
        </ul>
      </div>

      <div className="mini-section">
        <h3>최근 성과</h3>
        {performance ? (
          <div className="performance-strip">
            <span>{performance.accuracy}</span>
            <span>{performance.strategyReturn}</span>
            <span>{performance.maxDrawdown}</span>
          </div>
        ) : (
          <div className="performance-strip muted">성과 데이터 없음</div>
        )}
      </div>
    </section>
  );
}

function Timeline({ project }) {
  const items = extractTimeline(project);
  return (
    <section className="panel timeline">
      <div className="section-title">
        <div>
          <span className="eyebrow">Timeline</span>
          <h3>{projectLabel(project.projectId)} recent reports</h3>
        </div>
        <span className="muted">{items.length} entries</span>
      </div>
      <div className="timeline-list">
        {items.map((item) => {
          const verdict = item.record_type === 'decision'
            ? item.decision_snapshot?.dashboard?.verdict
            : item.data?.dashboard?.verdict;
          const tone = verdictClass(verdict);
          const label = item.record_type === 'decision' ? 'decision' : 'report';
          return (
            <div key={`${item.record_key}`} className="timeline-item">
              <div>
                <div className="timeline-date">{formatDate(item.generated_at)}</div>
                <div className="timeline-sub">{label}</div>
              </div>
              <div className="timeline-right">
                <span className={`badge badge-${tone}`}>{verdict || 'unknown'}</span>
                <span className="timeline-key">{item.record_key}</span>
              </div>
            </div>
          );
        })}
      </div>
    </section>
  );
}

function App() {
  const [capital, setCapital] = React.useState(10000000);
  const [marketWeight, setMarketWeight] = React.useState(0.5);
  const [cryptoWeight, setCryptoWeight] = React.useState(0.5);
  const [cycles, setCycles] = React.useState(5);

  const [marketProject, cryptoProject] = snapshot.projects;
  const marketMetrics = getDecisionMetrics(marketProject);
  const cryptoMetrics = getDecisionMetrics(cryptoProject);
  const marketPerformance = summarizePerformance(marketProject.performance);
  const cryptoPerformance = summarizePerformance(cryptoProject.performance);

  const blendedMarket = clamp(marketWeight, 0, 1);
  const blendedCrypto = clamp(cryptoWeight, 0, 1);
  const normalizedTotal = blendedMarket + blendedCrypto || 1;
  const marketShare = blendedMarket / normalizedTotal;
  const cryptoShare = blendedCrypto / normalizedTotal;

  const marketProjected = projectedReturn(
    capital,
    marketShare,
    0,
    marketMetrics.expectedReturn * marketMetrics.recommendedMultiplier * (marketMetrics.confidence / 100),
    0,
    cycles
  );

  const cryptoProjected = projectedReturn(
    capital,
    0,
    cryptoShare,
    0,
    cryptoMetrics.expectedReturn * cryptoMetrics.recommendedMultiplier * (cryptoMetrics.confidence / 100),
    cycles
  );

  const blendedProjected = projectedReturn(
    capital,
    marketShare,
    cryptoShare,
    marketMetrics.expectedReturn * marketMetrics.recommendedMultiplier * (marketMetrics.confidence / 100),
    cryptoMetrics.expectedReturn * cryptoMetrics.recommendedMultiplier * (cryptoMetrics.confidence / 100),
    cycles
  );

  const currentPortfolioEdge =
    marketMetrics.expectedReturn * marketMetrics.recommendedMultiplier * (marketMetrics.confidence / 100) * marketShare +
    cryptoMetrics.expectedReturn * cryptoMetrics.recommendedMultiplier * (cryptoMetrics.confidence / 100) * cryptoShare;

  const safeCapital = capital > 0 ? capital : 1;

  const latestGeneratedAt = snapshot.generatedAt || marketProject.latestDecision?.generated_at || cryptoProject.latestDecision?.generated_at;

  return (
    <main className="shell">
      <div className="ambient ambient-a" />
      <div className="ambient ambient-b" />

      <header className="hero">
        <div>
          <span className="eyebrow">Live control room</span>
          <h1>투자 리포트 모니터링</h1>
          <p className="hero-copy">
            마켓과 크립토 신호를 한 화면에 묶고, 실제 투자금 기준의 예상 수익 범위를 바로 볼 수 있는 관제판입니다.
          </p>
        </div>
        <div className="hero-meta">
          <div>
            <span>Snapshot</span>
            <strong>{formatDate(latestGeneratedAt)}</strong>
          </div>
          <div>
            <span>Region</span>
            <strong>{snapshot.region}</strong>
          </div>
          <div>
            <span>Source</span>
            <strong>{snapshot.source}</strong>
          </div>
        </div>
      </header>

      <section className="deck">
        <ReportCard project={marketProject} />
        <ReportCard project={cryptoProject} />
      </section>

      <section className="workspace">
        <div className="panel simulator">
          <div className="section-title">
            <div>
              <span className="eyebrow">Capital simulator</span>
              <h3>실투입 예상 수익률</h3>
            </div>
            <span className="muted">리포트 1사이클 기준 보정값</span>
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
                value={marketWeight * 100}
                onChange={(e) => setMarketWeight(toNumber(e.target.value, 50) / 100)}
              />
              <strong>{Math.round(marketShare * 100)}%</strong>
            </label>
            <label className="field">
              <span>크립토 비중</span>
              <input
                type="range"
                min="0"
                max="100"
                value={cryptoWeight * 100}
                onChange={(e) => setCryptoWeight(toNumber(e.target.value, 50) / 100)}
              />
              <strong>{Math.round(cryptoShare * 100)}%</strong>
            </label>
            <label className="field">
              <span>시나리오 기간</span>
              <select value={cycles} onChange={(e) => setCycles(toNumber(e.target.value, 5))}>
                <option value="1">1 cycle</option>
                <option value="5">5 cycles</option>
                <option value="20">20 cycles</option>
              </select>
            </label>
          </div>

          <div className="projection-grid">
            <div className="projection">
              <span>Market only</span>
              <strong>{currency(marketProjected - capital)}</strong>
              <small>{pct((marketProjected / safeCapital - 1) * 100, 2)}</small>
            </div>
            <div className="projection">
              <span>Crypto only</span>
              <strong>{currency(cryptoProjected - capital)}</strong>
              <small>{pct((cryptoProjected / safeCapital - 1) * 100, 2)}</small>
            </div>
            <div className="projection projection-accent">
              <span>Blended portfolio</span>
              <strong>{currency(blendedProjected - capital)}</strong>
              <small>{pct((blendedProjected / safeCapital - 1) * 100, 2)}</small>
            </div>
          </div>

          <div className="risk-band">
            <div>
              <span>보정 기대수익률</span>
              <strong>{pct(currentPortfolioEdge, 2)}</strong>
            </div>
            <div>
              <span>예상 평가금액</span>
              <strong>{moneyShort(blendedProjected)}</strong>
            </div>
          </div>

          <div className="tiny-note">
            이 수치는 현재 리포트의 예상값과 신뢰도, 권장 비중을 곱해 만든 시나리오입니다. 절대 수익 보장은 아니고, 비교용 의사결정 지표입니다.
          </div>
        </div>

        <div className="side-stack">
          <section className="panel">
            <div className="section-title">
              <div>
                <span className="eyebrow">Live signals</span>
                <h3>현재 핵심 지표</h3>
              </div>
            </div>

            <SignalRow label="SPY" value={pct(marketProject.latestDecision?.decision_snapshot?.core_data?.spy_1mo_pct || 0)} tone="good" />
            <SignalRow label="VIX" value={toNumber(marketProject.latestDecision?.decision_snapshot?.core_data?.vix || 0).toFixed(2)} tone="caution" />
            <SignalRow label="BTC" value={pct(cryptoProject.latestDecision?.decision_snapshot?.core_data?.btc_7d || 0)} tone="caution" />
            <SignalRow label="ETH" value={pct(cryptoProject.latestDecision?.decision_snapshot?.core_data?.eth_7d || 0)} tone="caution" />
            <SignalRow label="DXY" value={toNumber(marketProject.latestDecision?.decision_snapshot?.core_data?.dxy || 0).toFixed(2)} tone="danger" />
          </section>

          <Timeline project={marketProject} />
          <Timeline project={cryptoProject} />
        </div>
      </section>

      <section className="bottom-grid">
        <section className="panel">
          <div className="section-title">
            <div>
              <span className="eyebrow">Backtest</span>
              <h3>사후 검증 요약</h3>
            </div>
          </div>
          <div className="backtest-grid">
            <div className="backtest">
              <span>Market</span>
              <strong>{marketPerformance?.accuracy || '-'}</strong>
              <small>{marketPerformance?.strategyReturn || '-'}</small>
              <small>{marketPerformance?.maxDrawdown || '-'}</small>
            </div>
            <div className="backtest">
              <span>Crypto</span>
              <strong>{cryptoPerformance?.accuracy || '-'}</strong>
              <small>{cryptoPerformance?.strategyReturn || '-'}</small>
              <small>{cryptoPerformance?.maxDrawdown || '-'}</small>
            </div>
          </div>
        </section>

        <section className="panel raw">
          <div className="section-title">
            <div>
              <span className="eyebrow">Raw snapshot</span>
              <h3>최근 판단 문장</h3>
            </div>
          </div>
          <div className="raw-stack">
            <article>
              <h4>Market</h4>
              <p>{marketProject.latestDecision?.decision_snapshot?.decision_brief?.[1] || 'No brief'}</p>
            </article>
            <article>
              <h4>Crypto</h4>
              <p>{cryptoProject.latestDecision?.decision_snapshot?.decision_brief?.[1] || 'No brief'}</p>
            </article>
          </div>
        </section>
      </section>
    </main>
  );
}

export default App;
