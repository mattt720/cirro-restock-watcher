"""state.json persistence and the pure decision core.

All alert, flicker-cap, and degraded-target rules live in `decide` (SPEC.md
"Design decisions"), which is side-effect-free: it never mutates its inputs and
returns a fresh per-target state alongside which alerts to send.
"""

import json
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

SCHEMA_VERSION = 1
RESTOCK_ALERT_COOLDOWN_S = 3600
DEGRADED_FAILURE_THRESHOLD = 12
DEGRADED_ALERT_COOLDOWN_S = 24 * 3600

_INITIAL_TARGET_STATE = {
    "state": "unknown",
    "last_known_stock_state": None,
    "consecutive_failures": 0,
    "last_alert_ts": None,
    "degraded_alerted_ts": None,
}


@dataclass(frozen=True)
class Decision:
    target_state: dict
    restock_alert: bool
    degraded_alert: bool
    transition: str | None


def decide(previous: dict, observation: str, now: datetime) -> Decision:
    """Apply one observation to one target's state. Pure — all I/O stays in the caller.

    Alert rule: fire on `in` when the last *known* stock state was not `in`
    (null counts as not-in — first observation alerts), capped at one alert per
    hour per target. `unknown` never overwrites last_known_stock_state and never
    alerts; 12 consecutive failures fire a degraded alert at most once per 24h.
    """
    if observation not in ("in", "out", "unknown"):
        raise ValueError(f"invalid observation: {observation!r}")

    failures = previous["consecutive_failures"] + 1 if observation == "unknown" else 0
    last_known = (
        previous["last_known_stock_state"] if observation == "unknown" else observation
    )

    restock_alert = (
        observation == "in"
        and previous["last_known_stock_state"] != "in"
        and _cooldown_elapsed(previous["last_alert_ts"], now, RESTOCK_ALERT_COOLDOWN_S)
    )
    degraded_alert = failures >= DEGRADED_FAILURE_THRESHOLD and _cooldown_elapsed(
        previous["degraded_alerted_ts"], now, DEGRADED_ALERT_COOLDOWN_S
    )

    new_target_state = {
        "state": observation,
        "last_known_stock_state": last_known,
        "consecutive_failures": failures,
        "last_alert_ts": now.isoformat() if restock_alert else previous["last_alert_ts"],
        "degraded_alerted_ts": (
            now.isoformat() if degraded_alert else previous["degraded_alerted_ts"]
        ),
    }
    transition = None if observation == previous["state"] else f"{previous['state']}→{observation}"
    return Decision(new_target_state, restock_alert, degraded_alert, transition)


def _cooldown_elapsed(last_ts: str | None, now: datetime, cooldown_s: int) -> bool:
    if last_ts is None:
        return True
    try:
        elapsed = (now - datetime.fromisoformat(last_ts)).total_seconds()
    except (TypeError, ValueError):
        # Hand-edited state.json may carry a naive or garbage timestamp; treat the
        # cooldown as elapsed (bias toward alerting) rather than crash the run.
        return True
    return elapsed >= cooldown_s


def initial_state() -> dict:
    return {
        "schema_version": SCHEMA_VERSION,
        "last_run": None,
        "last_heartbeat_date": None,
        "targets": {},
    }


def load_state(path: str | Path) -> dict:
    """Load state.json; an absent, unreadable, or malformed file is first-run state.

    Resetting on corruption is deliberate: it may re-alert on an already-in-stock
    target, and the spec biases toward a cheap duplicate ping over a missed restock.
    """
    path = Path(path)
    if not path.exists():
        return initial_state()
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, UnicodeDecodeError):
        print(f"warning: {path} is unreadable or malformed; starting from first-run state")
        return initial_state()
    if not isinstance(data, dict) or not isinstance(data.get("targets"), dict):
        print(f"warning: {path} has an unexpected shape; starting from first-run state")
        return initial_state()
    return data


def save_state(path: str | Path, state: dict) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(state, indent=2) + "\n", encoding="utf-8")


def target_state(state: dict, target_id: str) -> dict:
    """Per-target state, defaulting unseen targets to all-null first-run state."""
    return dict(state["targets"].get(target_id, _INITIAL_TARGET_STATE))
