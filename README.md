# Market Monitor Dashboard

Standalone dashboard for monitoring the market-agent and crypto-agent cloud data.

## What it shows

- Latest daily verdicts for market and crypto
- Decision confidence, position sizing, and expected return
- Recent timeline of reports and decisions
- Backtest summary from the latest performance snapshots
- Capital simulator with blended return projection

## Local development

```bash
npm install
npm run dev
```

## Data sync

The app snapshots the latest DynamoDB and S3 data into `src/data/snapshot.json`.

```bash
npm run sync:data
```

If AWS access is unavailable, the build falls back to the committed snapshot.

## Build

```bash
npm run build
```

