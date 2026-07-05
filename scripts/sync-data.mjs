import { execFileSync } from 'node:child_process';
import { mkdirSync, readFileSync, writeFileSync } from 'node:fs';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';

const __dirname = dirname(fileURLToPath(import.meta.url));
const root = join(__dirname, '..');
const dataDir = join(root, 'src', 'data');
const snapshotPath = join(dataDir, 'snapshot.json');
const region = process.env.AWS_REGION || process.env.AWS_DEFAULT_REGION || 'ap-northeast-2';

const projects = [
  {
    key: 'market',
    projectId: 'market-agent',
    historyTable: 'market-agent-history',
    decisionTable: 'market-agent-decisions',
    performanceBucket: 'market-agent-daily-performance-471112665443-ap-northeast-2',
    performancePrefix: 'performance/',
  },
  {
    key: 'crypto',
    projectId: 'crypto-agent',
    historyTable: 'crypto-agent-history',
    decisionTable: 'crypto-agent-decisions',
    performanceBucket: 'crypto-agent-daily-performance-471112665443-ap-northeast-2',
    performancePrefix: 'performance/',
  },
];

function emptyProjectSnapshot(projectId) {
  return {
    projectId,
    reportCount: 0,
    decisionCount: 0,
    latestReport: null,
    latestDecision: null,
    reports: [],
    decisions: [],
    performance: {
      report_text: null,
      report_json: null,
    },
    performance_history: [],
    performanceObjectKey: null,
    performanceMarkdownKey: null,
  };
}

function run(cmd, args) {
  return execFileSync(cmd, args, {
    encoding: 'utf8',
    stdio: ['ignore', 'pipe', 'pipe'],
    maxBuffer: 20 * 1024 * 1024,
    timeout: 30000,
    env: {
      ...process.env,
      AWS_EC2_METADATA_DISABLED: 'true',
      AWS_PAGER: '',
    },
  });
}

function tryRun(cmd, args) {
  try {
    return run(cmd, args);
  } catch (error) {
    return null;
  }
}

function parseJson(value) {
  return JSON.parse(value);
}

function fromDynamo(node) {
  if (node == null || typeof node !== 'object') return node;
  if ('S' in node) return node.S;
  if ('N' in node) return Number(node.N);
  if ('BOOL' in node) return node.BOOL;
  if ('NULL' in node) return null;
  if ('L' in node) return node.L.map(fromDynamo);
  if ('M' in node) {
    const out = {};
    for (const [key, value] of Object.entries(node.M)) out[key] = fromDynamo(value);
    return out;
  }
  if ('SS' in node) return [...node.SS];
  if ('NS' in node) return node.NS.map(Number);
  const out = {};
  for (const [key, value] of Object.entries(node)) out[key] = fromDynamo(value);
  return out;
}

function compactDecisionSnapshot(snapshot) {
  if (!snapshot) return null;
  return {
    dashboard: snapshot.dashboard || null,
    engine: snapshot.engine || null,
    calibration: snapshot.calibration || null,
    // REQ-C1: 백엔드 산출 신뢰 등급을 대시보드까지 전달
    trust_grade: snapshot.trust_grade || null,
    ai_status: snapshot.ai_status || null,
    core_data: snapshot.core_data || null,
    core_evidence: snapshot.core_evidence || [],
    buffers: snapshot.buffers || [],
    decision_brief: snapshot.decision_brief || [],
    ai_signal: snapshot.ai_signal || null,
  };
}

function normalizeReportText(reportText) {
  if (reportText === null || reportText === undefined) return reportText;
  const text = String(reportText);
  const failureMarker = '\n\n[AI 리포트 생성 실패:';
  const markerIndex = text.indexOf(failureMarker);
  const normalized = markerIndex >= 0 ? text.slice(0, markerIndex) : text;
  return normalized.replace(/\s+$/u, '');
}

function compactReport(record) {
  if (!record) return null;
  return {
    record_type: record.record_type || 'report',
    record_key: record.record_key || null,
    generated_at: record.generated_at,
    report_text: normalizeReportText(record.report_text),
  };
}

function compactDecision(record) {
  if (!record) return null;
  return {
    record_type: record.record_type || 'decision',
    record_key: record.record_key || null,
    generated_at: record.generated_at,
    decision_snapshot: compactDecisionSnapshot(record.decision_snapshot),
  };
}

function queryTable(tableName, projectId) {
  const payload = {
    ':p': { S: projectId },
  };
  const out = run('aws', [
    'dynamodb',
    'query',
    '--region',
    region,
    '--table-name',
    tableName,
    '--key-condition-expression',
    'project_id = :p',
    '--expression-attribute-values',
    JSON.stringify(payload),
    '--output',
    'json',
  ]);
  const data = parseJson(out);
  return (data.Items || []).map(fromDynamo);
}

function downloadS3Objects(bucket, prefix, suffix) {
  const listing = run('aws', [
    's3api',
    'list-objects-v2',
    '--region',
    region,
    '--bucket',
    bucket,
    '--prefix',
    prefix,
    '--output',
    'json',
  ]);
  const parsed = parseJson(listing);
  const keys = (parsed.Contents || [])
    .map((item) => item.Key)
    .filter((key) => key.endsWith(suffix))
    .sort();
  return keys.map((key) => ({
    key,
    content: run('aws', ['s3', 'cp', `s3://${bucket}/${key}`, '-']),
  }));
}

function makeProjectSnapshot(project) {
  const reports = queryTable(project.historyTable, project.projectId);
  const decisions = queryTable(project.decisionTable, project.projectId);
  const latestReport = reports.at(-1) || null;
  const latestDecision = decisions.at(-1) || null;
  const perfJsonObjects = downloadS3Objects(project.performanceBucket, project.performancePrefix, '.json');
  const perfMdObjects = downloadS3Objects(project.performanceBucket, project.performancePrefix, '.md');
  const perfJson = perfJsonObjects.at(-1) || null;
  const perfMd = perfMdObjects.at(-1) || null;
  const performanceHistory = perfJsonObjects.map((item) => {
    try {
      return {
        object_key: item.key,
        ...JSON.parse(item.content),
      };
    } catch {
      return {
        object_key: item.key,
        raw: item.content,
      };
    }
  });
  let performance = null;
  if (perfJson) {
    try {
      performance = JSON.parse(perfJson.content);
    } catch {
      performance = { raw: perfJson.content };
    }
  }
  const performanceSummary = performance?.report_text || perfMd?.content || null;
  return {
    projectId: project.projectId,
    reportCount: reports.length,
    decisionCount: decisions.length,
    latestReport: compactReport(latestReport),
    latestDecision: compactDecision(latestDecision),
    reports: reports.map(compactReport),
    decisions: decisions.map(compactDecision),
    performance: {
      report_text: performanceSummary,
      report_json: performance || null,
    },
    performance_history: performanceHistory,
    performanceObjectKey: perfJson ? perfJson.key : null,
    performanceMarkdownKey: perfMd ? perfMd.key : null,
  };
}

function syncSnapshot() {
  mkdirSync(dataDir, { recursive: true });
  try {
    const snapshot = {
      generatedAt: new Date().toISOString(),
      region,
      source: 'aws',
      projects: projects.map(makeProjectSnapshot),
    };
    writeFileSync(snapshotPath, `${JSON.stringify(snapshot, null, 2)}\n`, 'utf8');
    process.stdout.write(`Wrote ${snapshotPath}\n`);
  } catch (error) {
    const fallback = (() => {
      try {
        const existing = JSON.parse(readFileSync(snapshotPath, 'utf8'));
        return {
          ...existing,
          generatedAt: existing.generatedAt || new Date().toISOString(),
          region: existing.region || region,
          source: 'local-snapshot',
          refreshWarning: String(error?.message || error),
        };
      } catch {
        return {
          generatedAt: new Date().toISOString(),
          region,
          source: 'local-snapshot',
          refreshWarning: String(error?.message || error),
          projects: projects.map((project) => emptyProjectSnapshot(project.projectId)),
        };
      }
    })();

    writeFileSync(snapshotPath, `${JSON.stringify(fallback, null, 2)}\n`, 'utf8');
    process.stdout.write(`Wrote ${snapshotPath} from local snapshot fallback\n`);
  }
}

syncSnapshot();
