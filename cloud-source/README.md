# Cloud Source

This folder mirrors the AWS Lambda source used by the monitoring pipeline.

## Layout
- `lambdas/market_agent`: Market agent Lambda source
- `lambdas/crypto_agent`: Crypto agent Lambda source
- `lambdas/rss_mailer`: RSS-to-email Lambda source
- `lambdas/rss_ingest`: Herald RSS-to-S3 Lambda source
- `lambdas/rss_ingest_bitcoin`: Bitcoin RSS-to-S3 Lambda source
- `lambdas/python_function`: Legacy hello-world Lambda source
- `secrets/`: secret-name templates only, no secret values
- Each Lambda folder includes its own `.env.example` with the exact environment variable names it expects.

## Secrets policy

Do not commit API keys, tokens, app passwords, or chat IDs here.
Put them in your secret store or repository secrets, and wire them in as environment variables at deploy time.
