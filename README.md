# Cirro Restock Watcher

[![watch](https://github.com/mattt720/cirro-restock-watcher/actions/workflows/watch.yml/badge.svg)](https://github.com/mattt720/cirro-restock-watcher/actions/workflows/watch.yml)

## What & why

Meaco's Cirro and Cirro+ portable air conditioners are sold out UK-wide, restocks are
unannounced, and units sell out in hours. This watcher polls the public Shopify stock
endpoints of three UK stockists — Meaco direct, Aircare Appliances, and Air Con
Centre — on a 5-minute GitHub Actions cron (in practice GitHub throttles this — see
Limitations) and fires a sleep-breaking Discord alert (plus an ntfy push) within one run
of any watched variant coming back in stock. It is built to run unattended for months,
so it also detects and reports its own death.

## How it works

```
GitHub Actions cron (5-min schedule; GitHub throttles it in practice — see Limitations)
  │
  ▼
fetch each target in targets.json ──── GET /products/<handle>.js (Shopify Ajax API)
  │
  ▼
three-valued state machine: in / out / unknown          watcher/state.py (pure)
  │   a failed or ambiguous fetch is "unknown" — never "out"
  │
  ├─ restock (in, was not in) ──▶ Discord @everyone (+ personal @) + ntfy urgent
  │     repeated 3× a minute apart — unmissable; ≤ 1 restock event/hour/target
  ├─ 12 consecutive failures ──▶ degraded-target alert              ≤ 1/24h/target
  │
  ▼
commit state.json to the `state` branch    audit trail + resets GitHub's 60-day timer
  │
  ▼
refresh the ntfy dead-man's-switch         fires only if the watcher goes silent
daily Discord heartbeat (~07:00 UTC)       its absence at breakfast = watcher is dead
```

The two notification channels are independent: a failed Discord send never blocks the
ntfy send, and vice versa. One broken target never crashes the run or suppresses the
others.

## Setup

1. **Fork this repo** (public — scheduled runs on standard runners are free).
2. **Discord webhook.** Create a dedicated alerts channel on your server, then
   *Channel settings → Integrations → Webhooks → New Webhook* and copy the URL.
   **The URL is a password** — anyone holding it can post to your channel. If it leaks,
   regenerate it in the same menu.
3. **ntfy topic.** Pick an unguessable topic name (e.g. `openssl rand -hex 12`).
   **The topic name is also a password** — anyone who knows it can read and spam your
   alerts. Rotate by picking a new name.
4. **Actions secrets.** In the repo: *Settings → Secrets and variables → Actions* →
   add `DISCORD_WEBHOOK_URL` and `NTFY_TOPIC`. Until both exist, runs stay green but
   log `not set; skipping` for each send. Optionally add `DISCORD_MENTION_USER_ID` —
   your *numeric* Discord user ID (Discord *Settings → Advanced → Developer Mode*
   on, then right-click your own name → *Copy User ID*) — to get a personal
   @mention on restock alerts; unlike @everyone, it pings even under Discord's
   "Suppress @everyone and @here" setting.
5. **iPhone.** Allow Discord through your Sleep/DND Focus
   (*Settings → Focus → Sleep → Apps → Allow*), and set the alerts channel to
   **"All Messages"** in Discord's notification settings — otherwise the `@everyone`
   push can be suppressed. Apple Watch mirroring covers the wrist tap. Install the
   ntfy app and subscribe to your topic (best-effort — see Limitations).
6. **State branch** (one-time):

   ```bash
   git switch --orphan state && echo '{"schema_version":1,"targets":{}}' > state.json \
     && git add state.json && git commit -m "init state" && git push -u origin state \
     && git switch main
   ```

7. **Enable Actions** on the fork, then prove the pipeline end-to-end:
   *Actions → watch → Run workflow* with `self_test` ticked, with Sleep Focus **on**.
   The Discord alert should break through audibly; ~2 minutes later the unrefreshed
   dead-man's-switch fires. Verify it server-side even if the phone stays quiet:

   ```bash
   curl -s "https://ntfy.sh/<your-topic>/json?poll=1&since=10m"
   ```

To watch different products, edit `targets.json` (`endpoint` is the product page URL
with `.js` appended; get `variant_ids` from that JSON). Validate any new target while
it is genuinely sold out: watched variants must show `available: false`. A product that
shows a permanent `available: true` is a continue-selling/preorder trap and will alert
forever.

## Death detection

GitHub does not notify you when scheduled runs fail, get dropped, or when it disables
the schedule outright — a dead watcher looks exactly like a quiet one. Two layers cover
this:

- **Dead-man's-switch (fast, best-effort).** Every run's final step schedules an ntfy
  message — "the watcher has stopped" — delayed by `DMS_DELAY` (set in `watch.yml`),
  published with a fixed sequence ID so each run *replaces* the pending one. It fires
  only when runs stop refreshing it, and it fires server-side, so it covers crashed
  runs, disabled workflows, revoked credentials, and GitHub-side scheduling death
  alike. The delay must stay wider than the worst real gap between scheduled runs
  (see Limitations) or it cries wolf.
- **Daily Discord heartbeat (guaranteed).** The first run after 07:00 UTC posts a
  one-line-per-target summary to Discord. **No morning heartbeat = check the Actions
  tab.** Because ntfy's iOS delivery is best-effort, this absence — on the reliable
  channel — is the guaranteed ≤24h dead-watcher signal.

## Design notes

- **Why a three-valued state machine.** A timeout, a bot challenge, or a redirect to an
  HTML error page says nothing about stock. Collapsing failures into "out" would make
  the next successful fetch look like a restock and fire a false alert; so failures are
  `unknown`, which never overwrites the last known stock state and never alerts. Twelve
  consecutive failures (~1 hour) raise a degraded-target alert instead — visibility
  without false positives.
- **Why a `state` branch.** Actions runs are stateless, so state must live somewhere.
  Committing `state.json` to an orphan branch keeps `main` clean, makes every
  transition a readable commit message (a free audit trail), and — because
  workflow-generated commits count as repository activity — resets GitHub's 60-day
  scheduled-workflow disablement timer. One mechanism, three jobs.
- **Why a dead-man's-switch.** Alerting on silence can't be done by the thing that went
  silent. ntfy's scheduled-delivery + sequence-ID replacement runs server-side, which
  makes "no runs for 3 hours" itself the trigger.
- **Bias toward alerting.** First observation of an in-stock target alerts; corrupt
  state resets to first-run and may re-alert; stock flicker re-alerts (capped at one
  per hour per target); every restock alert repeats twice more at one-minute spacing
  (a single 4am ping is missable, and the repeats double as retries after a failed
  send). A duplicate ping costs a glance; a missed restock defeats the entire
  project.
- **Stdlib-only runtime.** `urllib.request`, `json`, `ssl`, `datetime` — no install
  step inside a job that runs every 5 minutes, no supply chain to audit, and the whole
  runtime fits in four small modules.

## Limitations

- **ntfy on iOS is best-effort.** DND/Focus bypass isn't wired up, notifications on
  current iOS may play no sound, and delivery reliability is the app's most-reported
  problem — which is why Discord is the primary channel. Track
  [ntfy issue #1680](https://github.com/binwiederhier/ntfy/issues/1680); if Critical
  Alerts ship on iOS, ntfy can be promoted back to primary.
- **GitHub throttles scheduled workflows — hard.** The workflow requests a 5-minute
  cron, but GitHub silently drops most high-frequency scheduled runs: this repo's
  first day live saw runs 2.5–4 hours apart, not 5 minutes. That is the real alert
  latency, and it is why `DMS_DELAY` in `watch.yml` is set well above the worst
  observed gap. If a restock window approaches and hours-scale latency isn't
  acceptable, drive the workflow from an external cron via `workflow_dispatch`
  instead of relying on `schedule`.
- **Alert-only.** No auto-purchase, no cart automation, no price tracking. You still
  have to wake up and click.
- **Shopify stockists only.** Meaco direct, Aircare Appliances, and Air Con Centre all
  expose the public `.js` product endpoint and are watched. John Lewis, Currys, AO,
  and Amazon sit behind bot protection with no clean public stock endpoint, and
  Argos shows stock only per postcode — all dropped by design; only Shopify
  storefronts can be added.
- **Bot challenges are an accepted risk.** If a stockist later blocks datacenter IPs,
  its fetches degrade to `unknown` and a degraded alert fires within ~1 hour — states
  are never falsely `in`/`out`.
- **The `state` branch accumulates commits** — one per run, by design (each resets the
  60-day timer). Squash it manually if the history bothers you; `main` stays clean.

## Development

```bash
pip install -e ".[dev]"   # pytest + ruff (the runtime itself has zero dependencies)
ruff check .
pytest
python -m watcher --self-test   # sends real notifications; needs the two env vars
```

State-machine rules, threat model, and the full design rationale live in
[`SPEC.md`](SPEC.md); durable invariants in [`CLAUDE.md`](CLAUDE.md).
