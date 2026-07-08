# Progress — Cirro Restock Watcher

> Session handoff doc. Delete during Phase 5 repo polish.
> Normative design: `SPEC.md`. Durable invariants: `CLAUDE.md`. This file is only "where we are".

## Status: Phases 0–3 complete ✅ — next up is Phase 4 (orchestration)

| Phase | State | Evidence |
|-------|-------|----------|
| 0 — Scaffold | ✅ done | commit `85380d2` |
| 1 — Probe + HUMAN GATE | ✅ passed 2026-07-05 | Actions run 28755447239 green; commits `08e8da9`, `e22659a` |
| 2 — Core logic | ✅ done | commit `23f0e79`; 72 tests green, ruff clean |
| 3 — Notifications | ✅ done 2026-07-08 | 126 tests green, ruff clean; live self-test deferred (see below) |
| 4 — Orchestration + watch.yml + state branch | ⬜ next | see "Next steps" below |
| 5 — README/docs polish | ⬜ | |

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
- `watcher/__main__.py` — `--self-test` sends one real Discord alert + urgent ntfy push on a
  clearly-labelled synthetic target, then schedules an unrefreshed 2-minute DMS (exit 0 all sent /
  1 any failed). A plain run exits 2 until Phase 4 lands the loop.
- Test webhook URLs use `discord.example`, not `discord.com` — the SPEC acceptance grep for
  committed webhook URLs must return nothing.

## Verify a fresh checkout

```bash
pip install -e ".[dev]"
python -m ruff check .    # ruff not on PATH in Git Bash — use python -m
python -m pytest -q       # expect 126 passed
```

## Decisions already made (don't re-litigate)

- Watch list is **3 targets** (user decision 2026-07-05), not the spec's assumed 4 — no "Cirro+ 12000"
  exists; user declined the 16000s. `targets.json` is the source of truth over SPEC.md's lineup.
- `meaco.online` excluded — distinct Shopify storefront, zero Cirro products.
- Local Windows Python fails TLS against meaco.com (stale trust store, old ISRG cross-sign path).
  **Do not weaken TLS.** Test parsing via fixtures; verify live behaviour on Actions runners.
- Git: conventional commits (`<type>: <description>`), no Co-Authored-By attribution.

## Next steps — Phase 4 (SPEC.md lines 150–157)

1. `watcher/__main__.py` run loop: load config → load state from `.state/state.json` → fetch each
   target (isolated try/except) → `decide` → send alerts → daily heartbeat (UTC hour ≥ 7 and
   `last_heartbeat_date` < today) → update `last_run` → save state → `refresh_dms()`. Exit non-zero
   only on `ConfigError` or unhandled internal crash. Keep `--self-test` working.
2. One-time orphan `state` branch bootstrap (command in SPEC.md "Commands").
3. `.github/workflows/watch.yml`: cron `4-59/5 * * * *` + `workflow_dispatch` (`self_test` bool
   input); `concurrency: {group: watcher, cancel-in-progress: false}`; `permissions: {contents:
   write}`; checkout `main` + second checkout `ref: state, path: .state`; setup-python 3.12;
   run with secrets as env; commit-and-push `.state/state.json` (every run commits — resets the
   60-day disablement timer).
4. Verification: two consecutive dispatch runs green, second loads first's state, `git log state`
   one commit per run, `main` clean. Then the live self-test (Sleep Focus on, Discord breaks
   through; DMS fires ~2 min later — poll `https://ntfy.sh/$NTFY_TOPIC/json?poll=1&since=10m`).

**Blocked on user before live verification (code is not blocked):**
- [ ] Create Discord webhook in a dedicated alerts channel → repo Actions secret `DISCORD_WEBHOOK_URL`
- [ ] Pick an unguessable ntfy topic (it's a password) → repo Actions secret `NTFY_TOPIC`
- [ ] iPhone: allow Discord through Sleep Focus, alerts channel → "All Messages"; install ntfy app,
      subscribe to the topic

Then Phase 5: README (6 sections), badge, final lint/test pass, delete this file.
