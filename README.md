# Market Monitor Dashboard

Standalone dashboard for monitoring the market-agent and crypto-agent cloud data.

## What it shows

- Latest daily verdicts for market and crypto
- Decision confidence, position sizing, and expected return
- Recent timeline of reports and decisions
- Backtest summary from the latest performance snapshots
- Ordinal / cost-weighted verdict analysis with tuned thresholds and hysteresis
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

AWS access is used when available to refresh data; if it is missing, the build falls back to the committed local snapshot.

## Build

```bash
npm run build
```

## Publish

The deployed GitHub Pages site is served from the committed `docs/` folder.
After rebuilding, copy the refreshed `dist/` output into `docs/` and push the
branch.

## Automatic refresh

GitHub Actions refreshes the dashboard every day at `08:00 KST` (`23:00 UTC`).
The workflow rebuilds the site, syncs the new `dist/` output into `docs/`, and
commits the updated snapshot back to `main`.

For live AWS-backed refreshes, set these repository secrets:

- `AWS_ACCESS_KEY_ID`
- `AWS_SECRET_ACCESS_KEY`

Without AWS credentials, the build cannot refresh data and the site will not
pick up new upstream reports.
