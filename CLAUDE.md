# Cirro Restock Watcher

Scheduled GitHub Actions watcher that polls Meaco's Shopify `.js` stock endpoints for
Cirro+ variants on a 5-minute cron and alerts via Discord webhook (primary) and ntfy
(secondary), with an ntfy dead-man's-switch and a daily Discord heartbeat. GitHub
throttles the cron hard in practice (observed gaps up to ~4h) — `DMS_DELAY` in
`watch.yml` must stay wider than the worst real gap between scheduled runs.

## Commands

```bash
pip install -e ".[dev]"   # setup (dev)
ruff check .              # lint
pytest                    # test
python -m watcher         # run (env: NTFY_TOPIC, DISCORD_WEBHOOK_URL; optional DMS_DELAY)
python -m watcher --self-test  # sends real notifications + schedules a 2-minute DMS
```

## Invariants (durable — do not violate)

- **Runtime code is stdlib-only.** `urllib.request`, `json`, `ssl`, `datetime` — no runtime
  dependencies, ever. pytest and ruff are dev-only.
- **A failed or ambiguous fetch is `unknown`, never `out`.** Timeout, non-200, redirect to
  HTML, malformed JSON, missing fields — all record `unknown`. `unknown` is never a stock
  change and never fires a restock alert by itself.
- **Secrets live only in GitHub Actions secrets** (`NTFY_TOPIC`, `DISCORD_WEBHOOK_URL`,
  optional `DISCORD_MENTION_USER_ID`), injected as env vars. Never hardcode, never
  log them.
- **Run state lives on the `state` branch, never on `main`.** Every run commits
  `state.json` there (this also resets GitHub's 60-day scheduled-workflow disablement
  timer).

## Architecture

cron (every 5 min) → fetch each target's Shopify `/products/{handle}.js` → three-valued
state machine (`in`/`out`/`unknown`) in `watcher/state.py` (pure, side-effect-free
decisions) → alerts in `watcher/notify.py` (Discord + ntfy, channels independent) →
state committed to the `state` branch. `SPEC.md` is the normative design document.
