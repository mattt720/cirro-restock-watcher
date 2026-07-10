"""python -m watcher entry point: the run loop (Phase 4) and --self-test.

One pass per invocation: fetch every target, apply the pure decision core, send
whatever alerts it returns, persist state, refresh the dead-man's-switch. Exit
is non-zero only for config errors or unhandled crashes — a failed channel send
is logged by the sender and must never fail the run.
"""

import os
import sys
import time
from dataclasses import dataclass
from datetime import UTC, date, datetime
from pathlib import Path

from watcher import notify, shopify
from watcher.config import ConfigError, Target, load_targets
from watcher.state import decide, initial_state, load_state, save_state, target_state

STATE_PATH = Path(".state/state.json")
HEARTBEAT_UTC_HOUR = 7
# A single 4am ping is missable; a restock repeats on both channels, spaced out
# so the phone sounds distinct notifications rather than collapsing a burst.
ALERT_FOLLOWUP_ROUNDS = 2
ALERT_FOLLOWUP_SPACING_S = 60

_REPO_URL = "https://github.com/mattt720/cirro-restock-watcher"

# Clearly labelled so a self-test ping can never be mistaken for a real restock.
_SELF_TEST_TARGET = Target(
    id="self-test",
    retailer="[SELF-TEST] no retailer — ignore",
    model="[SELF-TEST] Cirro watcher channel check",
    product_url=_REPO_URL,
    endpoint=_REPO_URL,
    variant_ids=(),
)


@dataclass(frozen=True)
class _TargetResult:
    target_id: str
    state: dict
    transition: str | None
    alerted: bool
    degraded: bool
    restock: bool


def run() -> int:
    """One watch pass — called every ~5 minutes by the workflow."""
    try:
        targets = load_targets()
    except ConfigError as exc:
        print(f"config error: {exc}")
        return 1

    now = _utcnow()
    # The orphan state branch starts as {"schema_version":1,"targets":{}}; fill
    # any missing top-level keys so that shape behaves as first-run state.
    state = {**initial_state(), **load_state(STATE_PATH)}
    print(f"loaded state: last_run={state['last_run']}")

    results = [_process_target(target, target_state(state, target.id), now) for target in targets]
    new_state = {
        **state,
        "targets": {**state["targets"], **{r.target_id: r.state for r in results}},
        "last_heartbeat_date": _heartbeat_date(state, results, now),
        "last_run": now.isoformat(),
    }
    save_state(STATE_PATH, new_state)
    _write_commit_message(results)
    notify.refresh_dms()
    restocked = [t for t, r in zip(targets, results, strict=True) if r.restock]
    _send_alert_followups(restocked)
    return 0


def _send_alert_followups(restocked: list[Target]) -> None:
    """Repeat every restock alert so it cannot be slept through. Runs strictly
    after the state commit and DMS refresh, so the sleeps never delay either;
    keyed on the restock decision, not send success, so the rounds double as
    retries when the initial sends failed."""
    if not restocked:
        return
    for _ in range(ALERT_FOLLOWUP_ROUNDS):
        time.sleep(ALERT_FOLLOWUP_SPACING_S)
        for target in restocked:
            notify.discord_alert(target)
            notify.ntfy_alert(target)


def _process_target(target: Target, previous: dict, now: datetime) -> _TargetResult:
    """Fetch and decide for one target; a crash here must never reach the others."""
    try:
        observation = shopify.fetch_target(target)
        decision = decide(previous, observation, now)
        alerted = False
        degraded = False
        if decision.restock_alert:
            # Separate statements, never `or`-chained: a failed Discord send must
            # not short-circuit the ntfy send (channel independence).
            discord_ok = notify.discord_alert(target)
            ntfy_ok = notify.ntfy_alert(target)
            alerted = discord_ok or ntfy_ok
        if decision.degraded_alert:
            discord_ok = notify.discord_degraded(target)
            ntfy_ok = notify.ntfy_degraded(target)
            degraded = discord_ok or ntfy_ok
    except Exception as exc:
        # Class name only: exception text can embed request URLs.
        print(f"{target.id}: unexpected error ({type(exc).__name__}); state carried forward")
        return _TargetResult(target.id, previous, None, False, False, False)
    line = f"{target.id}: {observation}"
    if decision.transition:
        line += f" {decision.transition}"
    if alerted:
        line += " alert-sent"
    if degraded:
        line += " degraded-alert-sent"
    print(line)
    return _TargetResult(
        target.id, decision.target_state, decision.transition, alerted, degraded,
        decision.restock_alert,
    )


def _heartbeat_date(state: dict, results: list[_TargetResult], now: datetime) -> str | None:
    """Daily Discord heartbeat at the first run after 07:00 UTC; its absence at
    breakfast is the guaranteed dead-watcher signal. Returns the date to persist."""
    previous = state["last_heartbeat_date"]
    today = now.date()
    if now.hour < HEARTBEAT_UTC_HOUR or not _heartbeat_due(previous, today):
        return previous
    summary = "\n".join(f"{r.target_id}: {r.state['state']}" for r in results)
    if notify.discord_heartbeat(summary):
        return today.isoformat()
    return previous  # failed send: leave the date so the next run retries


def _heartbeat_due(previous: object, today: date) -> bool:
    """Only "already sent today" suppresses the heartbeat. Missing, garbage, or
    future dates (state.json is hand-editable on the state branch) all count as
    due — bias toward signalling life, mirroring the cooldowns in watcher.state."""
    if not isinstance(previous, str):
        return True
    try:
        return date.fromisoformat(previous) != today
    except ValueError:
        return True


def _write_commit_message(results: list[_TargetResult]) -> None:
    """The workflow commits state with this message — the audit trail of transitions."""
    path = os.environ.get("COMMIT_MESSAGE_FILE")
    if not path:
        return
    Path(path).write_text(_commit_message(results) + "\n", encoding="utf-8")


def _commit_message(results: list[_TargetResult]) -> str:
    changes = [
        _change_entry(r) for r in results if r.transition or r.alerted or r.degraded
    ]
    if not changes:
        return "run: no state changes"
    others = "; others unchanged" if len(changes) < len(results) else ""
    return "run: " + "; ".join(changes) + others


def _change_entry(result: _TargetResult) -> str:
    # Degraded alerts fire on unknown→unknown (no transition), so they need their
    # own marker or they would vanish from the audit trail entirely.
    entry = result.target_id
    if result.transition:
        entry += f" {result.transition}"
    if result.alerted:
        entry += " ALERTED"
    if result.degraded:
        entry += " DEGRADED"
    return entry


def _utcnow() -> datetime:
    return datetime.now(UTC)


def self_test() -> int:
    """Prove both channels and the DMS end-to-end with real sends."""
    print(
        "self-test: sending one Discord alert and one urgent ntfy push, then "
        "scheduling a 2-minute dead-man's-switch that nothing will refresh"
    )
    results = (
        notify.discord_alert(_SELF_TEST_TARGET),
        notify.ntfy_alert(_SELF_TEST_TARGET),
        notify.refresh_dms("2m", sequence_id=notify.DMS_SELF_TEST_SEQUENCE_ID),
    )
    if all(results):
        print("self-test: all sends succeeded — the watcher-dead message should fire in ~2 minutes")
        return 0
    print("self-test: one or more sends failed (see the lines above)")
    return 1


def main(argv: list[str]) -> int:
    if argv == ["--self-test"]:
        return self_test()
    if argv:
        print("usage: python -m watcher [--self-test]")
        return 2
    return run()


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
