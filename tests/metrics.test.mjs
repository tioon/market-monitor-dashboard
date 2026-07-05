// node --test tests/ 로 실행 (REQ-E1 프론트 계산 유틸 검증)
import test from 'node:test';
import assert from 'node:assert/strict';
import {
  DEFAULT_HIGH_THRESHOLD,
  DEFAULT_LOW_THRESHOLD,
  computeMaxDrawdownPct,
  evaluateVerdictRowsWalkForward,
  extractTrustGrade,
  normalizeVerdict,
  optimizeVerdictThresholds,
  scoreToVerdict,
  summarizeReturnRows,
  trustBadge,
  verdictCost,
  wilsonInterval,
} from '../src/lib/metrics.js';

function makeRow(day, score, actual) {
  return { generated_at: `2026-06-${String(day).padStart(2, '0')}T08:00:00`, predicted_score: score, actual_verdict: actual };
}

test('normalizeVerdict: 표준화', () => {
  assert.equal(normalizeVerdict('위험 우위'), '위험 우위');
  assert.equal(normalizeVerdict('혼조'), '주의');
  assert.equal(normalizeVerdict('중립'), '중립~우호');
  assert.equal(normalizeVerdict(''), null);
  assert.equal(normalizeVerdict('???'), null);
});

test('verdictCost: 거리 기반 비용', () => {
  assert.equal(verdictCost('중립~우호', '중립~우호'), 0);
  assert.equal(verdictCost('중립~우호', '주의'), 1);
  assert.equal(verdictCost('중립~우호', '위험 우위'), 3);
  assert.equal(verdictCost('중립~우호', null), null);
});

test('scoreToVerdict: 백엔드 룰 기본 임계값(3/6)과 일치', () => {
  assert.equal(scoreToVerdict(6, DEFAULT_LOW_THRESHOLD, DEFAULT_HIGH_THRESHOLD), '위험 우위');
  assert.equal(scoreToVerdict(3, DEFAULT_LOW_THRESHOLD, DEFAULT_HIGH_THRESHOLD), '주의');
  assert.equal(scoreToVerdict(2.9, DEFAULT_LOW_THRESHOLD, DEFAULT_HIGH_THRESHOLD), '중립~우호');
});

test('wilsonInterval: 표본이 늘면 구간이 좁아진다 (REQ-B2)', () => {
  const [low10] = wilsonInterval(0.6, 10);
  const [low100] = wilsonInterval(0.6, 100);
  assert.ok(low100 > low10);
  const [lo, hi] = wilsonInterval(0.6, 16);
  assert.ok(lo < 0.6 && hi > 0.6);
});

test('REQ-B3: actual 없는 행은 분모에서 제외', () => {
  const rows = [
    makeRow(1, 0, '중립~우호'),
    makeRow(2, 0, null),
    makeRow(3, 0, undefined),
    makeRow(4, 0, '중립~우호'),
  ];
  const result = evaluateVerdictRowsWalkForward(rows);
  assert.equal(result.count, 2);
  assert.equal(result.excludedCount, 2);
  assert.equal(result.exactAccuracy, 1);
});

test('REQ-B1: walk-forward — 훈련 표본 부족 시 기본 임계값 사용, 인샘플 최적화보다 낙관적이지 않음', () => {
  // score와 actual이 거의 무관한 노이즈 데이터
  const rows = [];
  const actuals = ['중립~우호', '위험 우위', '주의', '중립~우호', '위험 우위', '주의', '위험 우위', '중립~우호', '주의', '중립~우호'];
  actuals.forEach((actual, index) => {
    rows.push(makeRow(index + 1, (index * 7) % 9, actual));
  });
  const walkForward = evaluateVerdictRowsWalkForward(rows);
  const inSample = optimizeVerdictThresholds(rows);
  // 인샘플 최적화 정확도는 walk-forward(OOS) 정확도의 상한이어야 한다
  assert.ok(inSample.exactAccuracy >= walkForward.exactAccuracy);
  assert.equal(walkForward.method, 'walk-forward');
});

test('REQ-B1: 완벽한 신호는 walk-forward에서도 높은 정확도', () => {
  // score 0 → 중립, 4 → 주의, 8 → 위험 (기본 임계값과 정합)
  const rows = [];
  for (let index = 0; index < 12; index += 1) {
    const kind = index % 3;
    const score = kind === 0 ? 0 : kind === 1 ? 4 : 8;
    const actual = kind === 0 ? '중립~우호' : kind === 1 ? '주의' : '위험 우위';
    rows.push(makeRow(index + 1, score, actual));
  }
  const result = evaluateVerdictRowsWalkForward(rows);
  assert.ok(result.exactAccuracy >= 0.8);
});

test('summarizeReturnRows / computeMaxDrawdownPct', () => {
  const rows = [
    { strategy_return_pct: 10, benchmark_return_pct: 5, exposure: 1 },
    { strategy_return_pct: -20, benchmark_return_pct: -10, exposure: 1 },
  ];
  const sum = summarizeReturnRows(rows);
  assert.equal(sum.strategy, -10);
  assert.equal(sum.count, 2);
  const dd = computeMaxDrawdownPct(rows);
  assert.ok(Math.abs(dd - -20) < 1e-9);
});

test('REQ-C1: trustBadge — 등급 정보 없으면 실전 참고 불가', () => {
  const badge = trustBadge(null);
  assert.equal(badge.usable, false);
  assert.match(badge.detail, /실전 참고 불가/);
});

test('REQ-C1: trustBadge — 검증 중 등급은 사유 노출', () => {
  const badge = trustBadge({
    grade: '검증 중',
    usable_for_trading: false,
    sample_count: 12,
    unmet_conditions: ['표본 부족 (12/60)'],
  });
  assert.equal(badge.usable, false);
  assert.match(badge.detail, /표본 부족/);
});

test('REQ-C1: trustBadge — 제한적 참고는 사용 가능 + 적중률 표시', () => {
  const badge = trustBadge({
    grade: '제한적 참고',
    usable_for_trading: true,
    sample_count: 70,
    oos_hit_rate: 0.58,
    oos_hit_lower_bound: 0.52,
  });
  assert.equal(badge.usable, true);
  assert.match(badge.detail, /OOS 적중률 58%/);
});

test('extractTrustGrade: 우선순위 (decision > calibration > performance)', () => {
  const fromDecision = extractTrustGrade({ trust_grade: { grade: 'A' } }, { trust_grade: { grade: 'B' } });
  assert.equal(fromDecision.grade, 'A');
  const fromPerf = extractTrustGrade({}, { calibration: { trust_grade: { grade: 'C' } } });
  assert.equal(fromPerf.grade, 'C');
  assert.equal(extractTrustGrade(null, null), null);
});
