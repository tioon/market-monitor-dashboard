import { execFileSync } from 'node:child_process';
import { mkdirSync, writeFileSync } from 'node:fs';
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

function run(cmd, args) {
  return execFileSync(cmd, args, {
    encoding: 'utf8',
    stdio: ['ignore', 'pipe', 'pipe'],
    maxBuffer: 20 * 1024 * 1024,
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
    core_data: snapshot.core_data || null,
    core_evidence: snapshot.core_evidence || [],
    buffers: snapshot.buffers || [],
    decision_brief: snapshot.decision_brief || [],
    ai_signal: snapshot.ai_signal || null,
  };
}

function compactReport(record) {
  if (!record) return null;
  return {
    record_type: record.record_type || 'report',
    record_key: record.record_key || null,
    generated_at: record.generated_at,
    report_text: record.report_text,
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

function downloadLatestS3Object(bucket, prefix, suffix) {
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
  if (keys.length === 0) return null;
  const key = keys[keys.length - 1];
  const content = run('aws', ['s3', 'cp', `s3://${bucket}/${key}`, '-']);
  return { key, content };
}

function makeProjectSnapshot(project) {
  const reports = queryTable(project.historyTable, project.projectId);
  const decisions = queryTable(project.decisionTable, project.projectId);
  const latestReport = reports.at(-1) || null;
  const latestDecision = decisions.at(-1) || null;
  const perfJson = downloadLatestS3Object(project.performanceBucket, project.performancePrefix, '.json');
  const perfMd = downloadLatestS3Object(project.performanceBucket, project.performancePrefix, '.md');
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
    throw error;
  }
}

syncSnapshot();
