# Progress — Cirro Restock Watcher

> Session handoff doc. Delete during Phase 5 repo polish.
> Normative design: `SPEC.md`. Durable invariants: `CLAUDE.md`. This file is only "where we are".

## Status: Phases 0–2 complete ✅ — next up is Phase 3 (notifications)

| Phase | State | Evidence |
|-------|-------|----------|
| 0 — Scaffold | ✅ done | commit `85380d2` |
| 1 — Probe + HUMAN GATE | ✅ passed 2026-07-05 | Actions run 28755447239 green; commits `08e8da9`, `e22659a` |
| 2 — Core logic | ✅ done | commit `23f0e79`; 72 tests green, ruff clean |
| 3 — Notifications | ⬜ next | see "Next steps" below |
| 4 — Orchestration + watch.yml + state branch | ⬜ | |
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

## Verify a fresh checkout

```bash
pip install -e ".[dev]"
python -m ruff check .    # ruff not on PATH in Git Bash — use python -m
python -m pytest -q       # expect 72 passed
```

## Decisions already made (don't re-litigate)

- Watch list is **3 targets** (user decision 2026-07-05), not the spec's assumed 4 — no "Cirro+ 12000"
  exists; user declined the 16000s. `targets.json` is the source of truth over SPEC.md's lineup.
- `meaco.online` excluded — distinct Shopify storefront, zero Cirro products.
- Local Windows Python fails TLS against meaco.com (stale trust store, old ISRG cross-sign path).
  **Do not weaken TLS.** Test parsing via fixtures; verify live behaviour on Actions runners.
- Git: conventional commits (`<type>: <description>`), no Co-Authored-By attribution.

## Next steps — Phase 3 (SPEC.md lines 141–148)

Build `watcher/notify.py` + `tests/test_notify.py`, TDD, stdlib-only (`urllib.request`):

1. `discord_alert(target)` — POST to `DISCORD_WEBHOOK_URL`: `content` with `@everyone` + IN STOCK,
   one embed (model/retailer/product URL), `allowed_mentions: {"parse": ["everyone"]}`.
2. `discord_degraded(target)` (no mention), `discord_heartbeat(summary)` (no mention).
3. `ntfy_alert(target)` — `Priority: urgent`, `Click:` product URL, rotating_light tag;
   `ntfy_degraded(target)` default priority; `refresh_dms(delay)` — POST
   `https://ntfy.sh/{NTFY_TOPIC}/watcher-dead` with `In: {delay}` header (default `3h`, env `DMS_DELAY`).
4. All senders: read env vars only, swallow+log own errors (channels independent), **never log
   secret values** (webhook URL, topic).
5. `--self-test` flag on `python -m watcher`: one real Discord alert, one urgent ntfy push, schedule
   a 2-minute DMS *without* refreshing it. (Minimal `__main__.py` arg handling now; full run loop is Phase 4.)
6. Tests mock the HTTP layer: assert exact URLs, payload shapes, headers (`In:`, `Priority:`,
   `Click:`, `allowed_mentions`), and that no secret appears in any log/print.

**Blocked on user before Phase 3 live verification (code + unit tests are not blocked):**
- [ ] Create Discord webhook in a dedicated alerts channel → repo Actions secret `DISCORD_WEBHOOK_URL`
- [ ] Pick an unguessable ntfy topic (it's a password) → repo Actions secret `NTFY_TOPIC`
- [ ] iPhone: allow Discord through Sleep Focus, alerts channel → "All Messages"; install ntfy app,
      subscribe to the topic

Then Phase 4 (SPEC.md lines 150–157): full `__main__.py` run loop, `watch.yml` (cron `4-59/5 * * * *`,
concurrency group, `contents: write`, checkout `state` branch into `.state/`), one-time orphan
`state` branch bootstrap. Phase 5: README (6 sections), badge, final lint/test pass.
