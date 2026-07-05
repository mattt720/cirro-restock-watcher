# Cirro Restock Watcher

> Target agent: **Claude Code**. Greenfield repo. Execute in a fresh session (`/clear` first).
> Phase 1 contains a hard human gate — do not proceed past it without the probe evidence described there.

## Goal

Ship a public-repo GitHub Actions watcher that polls Meaco's Shopify stock endpoints for the four watched Cirro+ variants every ~5 minutes and, within one run of any variant flipping to in-stock, delivers a sleep-breaking Discord alert (iPhone + Apple Watch) plus an ntfy push — while surfacing its own death via an ntfy dead-man's-switch and a daily Discord heartbeat.

## Context

- **Repo state:** Greenfield — no code, no CLAUDE.md. Phase 0 creates the durable conventions file before any feature work.
- **Why now:** The Cirro line is sold out UK-wide; Meaco direct shows a ~September 2026 restock horizon, retailer channels may restock sooner and independently. Units sell out in hours. The watcher must survive months of unattended operation, which makes the 60-day scheduled-workflow disablement rule and silent-death detection live requirements, not theoretical ones.
- **User environment:** iPhone + Apple Watch (worn overnight); user runs their own Discord server. This drove the channel design below.
- **Files in play:** None yet. This spec is self-contained; all external facts below were verified 2026-07 via research-protocol plus follow-up checks (Shopify Ajax API, GitHub Actions policy, ntfy features and iOS app state, Discord webhooks).
- **Reference patterns:** No repo patterns exist. Follow the conventions established in Phase 0's CLAUDE.md.

Verified facts the design depends on:
- Shopify Ajax Product API: `GET /products/{handle}.js` returns product JSON including per-variant `available` booleans. Current and documented. Meaco direct (`meaco.com`) is confirmed Shopify and currently shows the correct sold-out signal (passes the brief's per-store validation rule today).
- GitHub: scheduled workflows in public repos are disabled after 60 days without repository activity; workflow-generated commits count as activity. Standard runners on public repos are free and unlimited (unaffected by the 2026 pricing changes). Scheduled runs can be delayed 10–30 min at peak, and GitHub does not notify on failed or dropped scheduled runs.
- ntfy: free, account-free. Scheduled messages published with a sequence ID (`POST /{topic}/{sequence_id}` + `In:` header) replace any pending message with the same ID — the documented dead-man's-switch pattern, executed server-side.
- **ntfy on iOS (verified 2026-07): unsuitable as the wake-up channel.** The maintainer's iOS stabilisation plan (issue #1680, Mar 2026) confirms DND/Focus bypass is not wired up (top-requested open feature), notifications on iOS 26.2+ currently play no sound, and delivery reliability is the app's most-reported problem. ntfy is therefore **secondary/best-effort** on this phone; it is retained for the server-side dead-man's-switch and channel redundancy.
- Discord: webhooks are free, need no bot and no new account; a plain HTTPS POST with an `@everyone` mention produces a normal APNs push. Discord's iOS delivery is reliable, and iOS Focus modes (including Sleep) allow designated apps through DND — allowing Discord in the Sleep Focus, with Watch mirroring, is the wake-up mechanism.
- John Lewis / AO: no clean public stock endpoint exists; dropped per the brief's reliability rule.

## Scope

**In scope:**
- Watch list of 4 targets on Meaco direct: Cirro+ 12000 and 14000, cooling-only and cooling+heating each (plus `meaco.online` if Phase 1 shows it's a distinct storefront).
- Three-valued state machine (in / out / unknown) with the alert rule, flicker cap, and degraded-target detection defined below.
- Discord webhook (primary, sleep-breaking) + ntfy push (secondary) alert channels.
- Death detection: ntfy dead-man's-switch (fast, automated) + daily Discord heartbeat (reliable channel).
- State persistence on a dedicated `state` branch (also neutralises the 60-day rule).
- Scheduled GitHub Actions workflow with overlap protection.
- Empirical runner-IP / signal-validation probe before any real code (Phase 1).
- Portfolio-quality README and repo hygiene.

**Out of scope:**
- Auto-purchase / cart automation — alert only.
- Price tracking — stock state only.
- Multi-user support or runtime configurability — single user, hardcoded `targets.json`.
- General scraping framework, HTML parsing, or headless browsers — Shopify `.js` JSON endpoints only.
- John Lewis, AO, or any non-Shopify retailer — dropped; adding Shopify-based stockists later is a follow-up, not this build.
- Email delivery — replaced by Discord with user approval (see Open questions).
- Retrying/queueing alerts if a notification service is down at send time — accepted residual risk (the two channels are independent).

## Stack & versions

| Component | Choice | Source | Why |
|-----------|--------|--------|-----|
| Language / runtime | Python 3.12, **stdlib-only runtime** (`urllib.request`, `json`, `ssl`, `datetime`) | user convention | Matches owner's established stdlib-Python pattern; zero install step keeps 5-min runs fast and the repo legible |
| Dev tooling | `pytest` + `ruff` (latest stable, dev-only, via `pyproject.toml` extras) | skill-defaulted | Conventional Python test/lint pair; runtime stays dependency-free |
| CI platform | GitHub Actions, `ubuntu-latest`, `actions/checkout@v4`, `actions/setup-python@v5` (Python 3.12) | user-specified | Required by the brief (free, unattended, public repo) |
| Schedule | cron `4-59/5 * * * *` (every 5 min at :04, :09, … — off the :00/:05 spikes) | brief-derived | Brief chose public repo specifically to afford a 5-min cadence; offset minutes per its scheduler-risk constraint |
| Overlap protection | Workflow `concurrency: { group: watcher, cancel-in-progress: false }` | skill-defaulted | Conventional Actions mechanism; serialises runs so state commits can't race |
| State persistence | `state.json` committed to a dedicated orphan `state` branch every run | research-decided | Sole option that satisfies persistence, main-branch legibility, **and** resets the 60-day disablement timer with one mechanism |
| Primary alert channel | Discord webhook (secret `DISCORD_WEBHOOK_URL`), `@everyone` mention + embed | user-specified | User's own server; reliable iOS/Watch delivery; breaks DND via Sleep-Focus allowed-apps; replaces the brief's email channel with user approval |
| Secondary alert channel | ntfy.sh push, topic in secret `NTFY_TOPIC`, restock alerts at `Priority: urgent` | brief-derived | Account-free redundancy; best-effort on iOS (verified caveats above); required anyway for the DMS |
| Death detection (fast) | ntfy dead-man's-switch: scheduled message, sequence ID `watcher-dead`, `In: 3h`, refreshed every run | research-decided | Fires on silence server-side; 3h window tolerates GitHub scheduler jitter. Best-effort delivery on iOS |
| Death detection (guaranteed) | Daily Discord heartbeat at the first run after 07:00 UTC | skill-defaulted | Reliable channel; its absence at breakfast is the ≤24h dead-watcher signal even if ntfy delivery fails |
| Config format | `targets.json` at repo root | skill-defaulted | World-readable is fine (no secrets in it); JSON parses with stdlib |

No runtime dependencies are introduced; the only new dev dependencies are pytest and ruff. `smtplib`/Gmail from the earlier draft is removed entirely.

## Design decisions (normative)

**State model.** Per target, `state.json` stores: `state` (`in`/`out`/`unknown`), `last_known_stock_state` (`in`/`out`/`null` — the most recent *non-unknown* observation; `unknown` never overwrites it), `consecutive_failures` (int), `last_alert_ts` (ISO 8601 or null), `degraded_alerted_ts` (ISO 8601 or null). Top level: `last_heartbeat_date` (UTC date string), `last_run` (ISO 8601 — updated every run so every run commits), `schema_version: 1`.

**Stock derivation.** A fetch is successful only if it returns HTTP 200 with valid JSON containing a `variants` list. Then: `in` if any variant in the target's `variant_ids` has `available: true` (empty `variant_ids` = any variant counts); else `out`. *Everything else* — timeout, non-200, redirect to HTML, malformed JSON, missing fields — records `unknown`. `unknown` is never a stock change and never triggers a restock alert by itself.

**Alert rule.** Fire a restock alert when `state == in` AND `last_known_stock_state != "in"` (null counts as not-in, so first observation and post-state-loss observations alert — bias toward alerting) AND (`last_alert_ts` is null OR ≥ 3600s ago — the once-per-hour flicker cap). Alerts go to both channels: Discord (`@everyone`, embed with model, retailer, clickable product URL) and ntfy (`Priority: urgent`, `Click:` header set to the product URL). A send failure on one channel must not prevent the other.

**Degraded-target rule.** On `consecutive_failures >= 12` (~1 hour at nominal cadence), send a degraded-target alert (Discord without `@everyone`, plus ntfy at default priority) and set `degraded_alerted_ts`; suppress repeats for 24h per target. Any successful fetch resets the counter. One broken target must never crash the run or suppress the others — every target is wrapped in its own try/except.

**Dead-man's-switch.** Every run, as its final step on success, POST to `https://ntfy.sh/{NTFY_TOPIC}/watcher-dead` with headers `In: {DMS_DELAY}` (env-configurable, default `3h`) and `Priority: high`, body explaining the watcher has stopped. Replacing by sequence ID means the pending alert only ever fires if runs stop refreshing it. Delivery to the iPhone is best-effort; the guaranteed dead-watcher signal is the *absence* of the daily Discord heartbeat, and the README documents this explicitly.

**Politeness.** One request per target per run, sequential, 10s timeout, User-Agent `cirro-restock-watcher/1.0 (+https://github.com/<owner>/<repo>)`.

**Logging.** Each run logs one summary line per target (`target_id: state [transition] [alert-sent]`) and the state-branch commit message summarises transitions (e.g. `run: 14k-cool out→in ALERTED; others unchanged`). Never log secret values (webhook URL, topic name).

## Repository layout

```
.github/workflows/probe.yml     # Phase 1: manual endpoint/IP probe
.github/workflows/watch.yml     # Phase 4: the scheduled watcher
watcher/
  __init__.py
  __main__.py                   # python -m watcher entry point
  config.py                     # load + validate targets.json
  shopify.py                    # fetch endpoint, derive in/out/unknown
  state.py                      # load/save/diff state.json, transition logic
  notify.py                     # discord webhook, ntfy publish, DMS refresh, heartbeat
scripts/probe.py                # Phase 1 probe (also runnable locally)
targets.json
tests/
  fixtures/                     # real captured .js payloads from Phase 1
  test_shopify.py
  test_state.py
  test_notify.py
CLAUDE.md
README.md
pyproject.toml
LICENSE                         # MIT
.gitignore
```

## Implementation plan

### Phase 0: Project setup (greenfield conventions)

- **Files:** `CLAUDE.md`, `pyproject.toml`, `.gitignore`, `LICENSE`, `README.md` (skeleton), empty `watcher/` and `tests/` packages.
- **Steps:**
  1. Create `pyproject.toml`: project metadata, `requires-python = ">=3.12"`, no runtime deps, `[project.optional-dependencies] dev = ["pytest", "ruff"]`, ruff config.
  2. Create `CLAUDE.md` with: build/lint/test commands (below), the architecture one-liner, and the durable invariants — *runtime code is stdlib-only; a failed or ambiguous fetch is `unknown`, never `out`; secrets live only in Actions secrets (`NTFY_TOPIC`, `DISCORD_WEBHOOK_URL`); run state lives on the `state` branch, never on `main`.*
  3. Create MIT `LICENSE`, `.gitignore` (Python defaults + `.state/`), README skeleton (title + one-liner only for now).
- **Verification:** `pip install -e ".[dev]" && ruff check . && pytest` exits 0 (no tests collected is acceptable at this phase).

### Phase 1: Empirical probe — runner IPs, endpoints, per-store validation (HUMAN GATE)

The research left runner-IP access as the one genuinely unverifiable item; this phase settles it before any real code, per the brief.

- **Files:** `scripts/probe.py`, `.github/workflows/probe.yml`, `targets.json` (finalised), `tests/fixtures/*.json`.
- **Steps:**
  1. Write `scripts/probe.py` (stdlib): for a hardcoded candidate list, fetch each `/products/{handle}.js` with the production User-Agent and 10s timeout; print HTTP status, product title, and each variant's `id`, `title`, `available`. Discover the actual Cirro+ 12000/14000 handles (cooling-only and cooling+heating) by fetching `meaco.com/products.json` or checking the live site; include the `meaco.online` equivalents to determine whether it's a distinct storefront or a redirect.
  2. Write `probe.yml`: `workflow_dispatch`-only, runs the probe on `ubuntu-latest`, uploads each raw JSON response as a build artifact.
  3. Run it. Download the artifacts into `tests/fixtures/` (these become the parser's test fixtures — real payloads, not invented ones).
  4. Confirm per-store validation: every candidate currently shows `available: false` on watched variants (the signal genuinely flips when sold out — verifiable now, while stock is out). Drop any candidate that shows a permanent `available: true` (continue-selling/preorder trap) or that challenges/blocks the runner.
  5. Write the final `targets.json`: array of `{id, retailer, model, product_url, endpoint, variant_ids}` for every candidate that passed.
- **Verification:** probe workflow run is green; its log shows HTTP 200 + parsed variants for every target kept in `targets.json`, each with `available: false` right now. **Stop here and show the user the probe results before Phase 2.** If meaco.com challenges GitHub runner IPs, the project's core assumption fails and the user must decide the fallback — do not improvise one.

### Phase 2: Core logic — pure, fixture-tested

- **Files:** `watcher/config.py`, `watcher/shopify.py`, `watcher/state.py`, `tests/test_shopify.py`, `tests/test_state.py`.
- **Steps:**
  1. `config.py`: load and schema-validate `targets.json`; fail loudly on malformed config (config errors are the one thing that *should* crash the run).
  2. `shopify.py`: `fetch_target(target) -> "in"|"out"|"unknown"` implementing the stock-derivation rules exactly as specified, catching all fetch/parse exceptions into `unknown`.
  3. `state.py`: load/save `state.json` (tolerating absent file → all-null first-run state), plus a pure `decide(target_state, observation, now) -> Decision` function returning which alerts to send and the updated per-target state. All alert/flicker/degraded rules live here, side-effect-free.
  4. Tests against the Phase 1 fixtures plus synthetic cases: in→in (no alert), out→in (alert), unknown→in (alert), null→in first observation (alert), in→unknown→in (no alert — unknown never overwrote `last_known_stock_state`), flicker inside 1h (suppressed), flicker after 1h (alert), 11 failures (no degraded alert), 12th failure (degraded alert), degraded repeat inside 24h (suppressed), success resets counter, malformed JSON → unknown, HTTP 500 → unknown.
- **Verification:** `pytest tests/test_shopify.py tests/test_state.py -v` exits 0 with all listed cases present and passing.

### Phase 3: Notification channels

- **Files:** `watcher/notify.py`, `tests/test_notify.py`.
- **Steps:**
  1. Implement in `notify.py`: `discord_alert(target)` (POST JSON to `DISCORD_WEBHOOK_URL`: `content` with `@everyone` + `IN STOCK` line, one embed carrying model/retailer/product URL, `allowed_mentions: {"parse": ["everyone"]}`), `discord_degraded(target)` (no mention), `discord_heartbeat(summary)` (no mention; one line per target state), `ntfy_alert(target)` (urgent priority, `Click:` header, rotating_light tag), `ntfy_degraded(target)`, and `refresh_dms(delay)` (sequence-ID `watcher-dead`, `In:` header). All read config from env vars only; each sender swallows and logs its own errors so channels stay independent.
  2. Add a `--self-test` flag to `python -m watcher` that sends one test Discord alert (with `@everyone`), one test ntfy push (urgent), and schedules a DMS with `DMS_DELAY=2m` *without* refreshing it — so the dead-alert fires 2 minutes later, proving the switch end-to-end.
  3. Unit tests mock the HTTP layer and assert exact URLs, payload shapes, and headers (`In:`, `Priority:`, `Click:`, `allowed_mentions`), and that no secret value ever appears in a log call.
- **Verification:** `pytest tests/test_notify.py -v` exits 0; then a `workflow_dispatch` self-test run (Phase 4 wires the dispatch input) with the phone's **Sleep/DND Focus enabled and Discord allowed through it** — observable outcome: the Discord alert breaks through audibly on iPhone and taps the Watch. The DMS firing is verified server-side: `curl -s "https://ntfy.sh/$NTFY_TOPIC/json?poll=1&since=10m"` shows the `watcher-dead` message ~2 minutes after the run (app receipt on iOS is recorded as informational, not gating).

### Phase 4: Orchestration, schedule, and state branch

- **Files:** `watcher/__main__.py`, `.github/workflows/watch.yml`.
- **Steps:**
  1. `__main__.py`: load config → load state from `.state/state.json` → fetch each target (isolated try/except) → apply `decide` → send alerts → daily heartbeat check (send `discord_heartbeat` when UTC hour ≥ 7 and `last_heartbeat_date` < today) → update `last_run` → write state → refresh DMS. Exit non-zero only on config errors or unhandled internal crashes.
  2. Create the orphan `state` branch once (`git switch --orphan state`, commit an initial `state.json`, push) — document this command in the README; the agent performs it here.
  3. `watch.yml`: triggers `schedule` (cron `4-59/5 * * * *`) and `workflow_dispatch` (with a `self_test` boolean input); `concurrency: { group: watcher, cancel-in-progress: false }`; `permissions: { contents: write }`; steps — checkout `main`, checkout `state` branch into `.state/` (second `actions/checkout@v4` with `ref: state`, `path: .state`), setup-python 3.12, run `python -m watcher` (secrets injected as env), then commit-and-push `.state/state.json` using the default `GITHUB_TOKEN` with the transition-summary message. Because `last_run` changes every run, every run commits — that guarantee is load-bearing: it is what resets the 60-day disablement timer.
- **Verification:** two consecutive `workflow_dispatch` runs are green; the second run's log shows it loaded the state written by the first (log the loaded state's `last_run`); `git log state --oneline` shows one commit per run; `main` has no state commits.

### Phase 5: Docs & portfolio polish

- **Files:** `README.md`, `CLAUDE.md` (final pass).
- **Steps:**
  1. README sections, in order: **What & why** (3 sentences max), **How it works** (ASCII architecture diagram: cron → fetch → state machine → Discord/ntfy, state branch, DMS + heartbeat), **Setup** (fork; create a Discord webhook in a dedicated alerts channel and treat the URL as a password; iPhone: allow Discord through the Sleep/DND Focus's allowed apps and set the alerts channel to "All Messages" — Watch mirroring covers the wrist tap; install the ntfy iOS app and subscribe to the topic, noting delivery there is best-effort; create an unguessable ntfy topic — the name is a password; add the two Actions secrets; create the `state` branch; enable Actions), **Death detection** (DMS explained + "no morning heartbeat in Discord = check the Actions tab" as the guaranteed signal), **Design notes** (why three-valued state, why a state branch, why a dead-man's-switch), **Limitations** (including the ntfy iOS caveats, with a pointer to ntfy issue #1680 — if Critical Alerts ship on iOS, ntfy can be promoted back to primary).
  2. Add a workflow status badge; final `ruff`/`pytest` pass.
- **Verification:** `grep -c '^## ' README.md` ≥ 6 with all six named sections present; `ruff check . && pytest` exits 0.

## Acceptance criteria

- [ ] `ruff check .` and `pytest` both exit 0; the state-machine test file covers every case enumerated in Phase 2 step 4.
- [ ] Phase 1 probe log shows HTTP 200 + per-variant `available` fields from a real GitHub runner for every target in `targets.json`, all currently `false`.
- [ ] A `workflow_dispatch` run of `watch.yml` completes green, logs one summary line per target, and pushes exactly one new commit to the `state` branch; `git log main` contains no state commits.
- [ ] Self-test run with Sleep/DND Focus active (Discord allowed through) delivers an audible Discord alert on iPhone and a Watch tap, containing model + retailer + clickable link; the ntfy urgent push is sent (HTTP 200 from ntfy.sh logged) with on-device receipt recorded as informational.
- [ ] DMS proof: after a self-test run schedules the unrefreshed 2-minute switch, `curl -s "https://ntfy.sh/$NTFY_TOPIC/json?poll=1&since=10m"` returns the fired `watcher-dead` message.
- [ ] Simulated restock: temporarily add a target pointing at any live in-stock Shopify product variant → the next run fires both channels; the run after that fires nothing (in→in); reverting the target restores normal operation.
- [ ] Flicker cap observable: with the simulated target, forcing out→in twice within an hour (edit state.json on the state branch) produces exactly one alert.
- [ ] `git grep -iE 'discord(app)?\.com/api/webhooks|ntfy\.sh/[a-z0-9]' -- ':!README.md'` returns nothing (README may show placeholder names only); workflow logs contain no secret values.
- [ ] Two scheduled runs execute back-to-back under the `concurrency` group without a failed state push (no race).

## Threat model

- **Trust boundary — retailer JSON is untrusted input.** Parse defensively with stdlib `json` only; never `eval`; cap response reads at 1 MB; malformed input degrades to `unknown`, never crashes the run.
- **Secrets.** `NTFY_TOPIC` (an ntfy topic is a bearer credential — anyone holding it can read and spam alerts) and `DISCORD_WEBHOOK_URL` (a bearer credential — anyone holding it can post to that channel) exist only as Actions secrets, injected as env vars, never echoed. GitHub masks secrets in logs, but code must not log them anyway. Both are cheaply revocable: regenerate the webhook in Discord channel settings; rotate the ntfy topic name.
- **Public repo exposure.** `targets.json` and the `state` branch are world-readable by design; they contain product URLs, stock states, and timestamps — no PII, no credentials. Nothing else may be persisted there.
- **`GITHUB_TOKEN`** is scoped to `contents: write` only.

## Observability

Health signalling is a core requirement, restated here as the complete signal set: per-run one-line-per-target logs; state-branch commit messages carrying transition summaries (a readable audit trail of every state change); degraded-target Discord + ntfy alerts after 12 consecutive failures (re-alert ≤ once/24h); a daily Discord heartbeat at ~7:00 UTC whose absence is the guaranteed ≤24h dead-watcher signal; and the 3-hour ntfy dead-man's-switch that fires server-side on total silence — covering crashed runs, disabled workflows, revoked webhooks, and GitHub-side scheduling death alike, with best-effort iOS delivery and a server-side poll available as ground truth.

## Risks & edge cases

- **meaco.com later enables bot challenges against datacenter IPs.** Fetches start failing → 12-failure degraded alert within ~1h; states go `unknown`, never falsely `out`/`in`. Accepted residual risk; probe verified it passes today.
- **GitHub scheduler congestion delays runs 10–30 min.** The 3h DMS window absorbs many consecutive delayed/dropped runs without false-alarming; alert latency degrades gracefully.
- **ntfy iOS delivery flakiness (verified, current).** Restock alerts don't depend on it — Discord is primary. The DMS depends on it for *push* delivery, which is why the daily Discord heartbeat exists as the guaranteed layer; residual risk is a dead watcher noticed at breakfast rather than at 3am, which meets the 24h requirement. Revisit if ntfy ships iOS Critical Alerts (issue #1680).
- **Discord outage or webhook revoked at the moment of restock.** ntfy is the independent second channel (may be delayed/silent on iOS but the message content and link still arrive); webhook send failures are logged and visible in Actions.
- **`@everyone` push suppressed by Discord notification settings.** Setup section requires the alerts channel set to "All Messages" and the self-test acceptance criterion proves breakthrough under Sleep Focus before the watcher goes live.
- **Stock flickers during checkout races.** Deliberately re-alerts (flicker is a buy signal), capped at 1/hour/target.
- **Product handle changes or page is deleted.** Fetch → `unknown` → degraded alert within ~1h; targets.json updated manually.
- **`inventory_policy: continue` trap (permanent `available: true`).** Excluded structurally by Phase 1's per-store validation; any target added later must repeat that validation (README setup note).
- **State branch accumulates thousands of commits.** By design — each commit resets the 60-day timer. `main` stays clean; the branch can be periodically squashed manually if desired (documented, not automated).
- **First run alerts on an already-in-stock target.** Intentional per the brief ("including first observation") — a duplicate ping is cheap, a missed restock is total failure.

## Commands

```bash
# Setup (dev)
pip install -e ".[dev]"

# Lint
ruff check .

# Test
pytest

# Run locally (requires env: NTFY_TOPIC, DISCORD_WEBHOOK_URL; optional DMS_DELAY)
python -m watcher

# Channel self-test (sends real notifications, schedules a 2-minute DMS)
python -m watcher --self-test

# One-time state branch bootstrap
git switch --orphan state && echo '{"schema_version":1,"targets":{}}' > state.json \
  && git add state.json && git commit -m "init state" && git push -u origin state && git switch main
```

## Open questions

None blocking. For the record, two approved deviations from the original brief:

1. **Email channel replaced by Discord webhook** — user-approved in review (avoids a second Gmail account and app-password management; conventional, fewer secrets, reliable iOS delivery).
2. **Primary push moved from ntfy to Discord** — forced by verified ntfy iOS limitations (no DND bypass, no-sound bug on current iOS, delivery reliability); ntfy retained as secondary and for the dead-man's-switch. If ntfy's iOS Critical Alerts ship (issue #1680), promoting ntfy back to primary is a one-line change in `notify.py` priorities plus a README update.