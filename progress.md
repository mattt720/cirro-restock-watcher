# Progress — Cirro Restock Watcher

> Session handoff doc. Delete during Phase 5 repo polish.
> Normative design: `SPEC.md`. Durable invariants: `CLAUDE.md`. This file is only "where we are".

## Status: Phases 0–4 complete ✅ — the watcher is LIVE; next up is Phase 5 (docs polish)

| Phase | State | Evidence |
|-------|-------|----------|
| 0 — Scaffold | ✅ done | commit `85380d2` |
| 1 — Probe + HUMAN GATE | ✅ passed 2026-07-05 | Actions run 28755447239 green; commits `08e8da9`, `e22659a` |
| 2 — Core logic | ✅ done | commit `23f0e79`; 72 tests green, ruff clean |
| 3 — Notifications | ✅ done 2026-07-08 | 126 tests green, ruff clean; live self-test deferred (see below) |
| 4 — Orchestration + watch.yml + state branch | ✅ done 2026-07-09 | commit `bee129c`; 142 tests green; dispatch runs 28982479235 + 28982504255 green — run 2 loaded run 1's `last_run`, one state-branch commit per run, `main` clean |
| 5 — README/docs polish | ⬜ next | see "Next steps" below |

Repo: https://github.com/mattt720/cirro-restock-watcher (public, `main` up to date with local).

## What exists now

- `watcher/config.py` — `load_targets()` → tuple of frozen `Target` dataclasses; raises `ConfigError`
  loudly on any malformed config (the one intended crash).
- `watcher/shopify.py` — `fetch_target(target)` / pure `derive_stock(raw, variant_ids)` →
  `"in" | "out" | "unknown"`. Anything failed or ambiguous is `unknown`, never `out`.
- `watcher/state.py` — `load_state`/`save_state` (absent/corrupt file → first-run state), pure
  `decide(previous, observation, now) -> Decision(target_state, restock_alert, degraded_alert, transition)`.
  All alert/flicker(1h)/degraded(12 fails, 24h cap) rules live here, side-effect-free.
- `tests/` — 72 tests: every SPEC.md Phase 2 case + review-hardening cases (hostile variant ids,
  naive/garbage timestamps in hand-edited state.json, non-bool `available` → unknown).
- `tests/fixtures/*.json` — real Meaco payloads captured from the Actions runner (all `available: false`).
- `targets.json` — final 3 targets: `12k-cool` (variant 58102727246211), `14k-cool` (58103889953155),
  `14k-heat` (58103896637827).
- `scripts/probe.py` + `.github/workflows/probe.yml` — Phase 1 probe (manual dispatch, keep).
- `watcher/notify.py` — 6 senders (`discord_alert`/`discord_degraded`/`discord_heartbeat`,
  `ntfy_alert`/`ntfy_degraded`/`refresh_dms`) through one `_post` helper; env vars read at call
  time; every sender swallows its own errors and returns bool; failures log only exception class
  name + HTTP code (urllib exceptions can embed the secret URL — never log `str(exc)`).
- `watcher/__main__.py` — the run loop (`python -m watcher`): load config → load state from
  `.state/state.json` (bootstrap-branch shape tolerated) → fetch each target (isolated
  try/except, crash logs class name only) → pure `decide` → alerts on both channels (separate
  statements, never `or`-chained — channel independence) → daily heartbeat (UTC hour ≥ 7, only
  "already sent today" suppresses; failed send retries next run) → save state → write the
  transition-summary commit message to `$COMMIT_MESSAGE_FILE` → `refresh_dms()` last. Exit
  non-zero only on `ConfigError`/crash. `--self-test` unchanged; unknown args exit 2.
- `.github/workflows/watch.yml` — cron `4-59/5 * * * *` + `workflow_dispatch` (`self_test` bool
  input runs `--self-test` instead and skips the state commit); concurrency group `watcher`
  (no cancel); `permissions: contents: write`; checkout `main` then `state`→`.state/`;
  commit step uses `git commit -F` (never shell-interpolates the message) and
  `git push origin HEAD:state` (sidesteps the checkout detached-HEAD gotcha).
- `state` branch — live on origin, one commit per run (this is what resets GitHub's 60-day
  scheduled-workflow disablement timer). Bootstrap was `{"schema_version":1,"targets":{}}`.
- Test webhook URLs use `discord.example`, not `discord.com` — the SPEC acceptance grep for
  committed webhook URLs must return nothing.
- Degraded alerts are marked `degraded-alert-sent` in run logs and `DEGRADED` in state-branch
  commit messages (review finding: unknown→unknown is not a transition, so they'd otherwise
  vanish from the audit trail).

## Verify a fresh checkout

```bash
pip install -e ".[dev]"
python -m ruff check .    # ruff not on PATH in Git Bash — use python -m
python -m pytest -q       # expect 142 passed
```

## Decisions already made (don't re-litigate)

- Watch list is **3 targets** (user decision 2026-07-05), not the spec's assumed 4 — no "Cirro+ 12000"
  exists; user declined the 16000s. `targets.json` is the source of truth over SPEC.md's lineup.
- `meaco.online` excluded — distinct Shopify storefront, zero Cirro products.
- Local Windows Python fails TLS against meaco.com (stale trust store, old ISRG cross-sign path).
  **Do not weaken TLS.** Test parsing via fixtures; verify live behaviour on Actions runners.
- Git: conventional commits (`<type>: <description>`), no Co-Authored-By attribution.

## Next steps — Phase 5 (SPEC.md lines 159–165)

1. README, six sections in order: What & why (≤3 sentences), How it works (ASCII diagram),
   Setup (webhook, ntfy topic, iPhone Focus config, secrets, state branch, enable Actions),
   Death detection (DMS + "no morning heartbeat = check Actions"), Design notes, Limitations
   (ntfy iOS caveats, pointer to ntfy issue #1680).
2. Workflow status badge; final `ruff`/`pytest` pass; delete this file.

**Blocked on user — the watcher is live but silent until these exist:**
- [ ] Create Discord webhook in a dedicated alerts channel → repo Actions secret `DISCORD_WEBHOOK_URL`
- [ ] Pick an unguessable ntfy topic (it's a password) → repo Actions secret `NTFY_TOPIC`
- [ ] iPhone: allow Discord through Sleep Focus, alerts channel → "All Messages"; install ntfy app,
      subscribe to the topic
- [ ] Then run the live self-test: Actions → watch → "Run workflow" with `self_test` ticked
      (Sleep Focus on — Discord must break through; the DMS fires ~2 min later — verify with
      `curl -s "https://ntfy.sh/$NTFY_TOPIC/json?poll=1&since=10m"`). Until the secrets exist,
      every run logs "not set; skipping" and the first run after they're added sends that day's
      heartbeat automatically.
