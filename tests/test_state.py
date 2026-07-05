"""State machine tests — every case enumerated in SPEC.md Phase 2 step 4."""

import json
from datetime import UTC, datetime, timedelta

from watcher.state import (
    Decision,
    decide,
    initial_state,
    load_state,
    save_state,
    target_state,
)

NOW = datetime(2026, 7, 5, 12, 0, 0, tzinfo=UTC)


def make_target_state(**overrides):
    base = {
        "state": "unknown",
        "last_known_stock_state": None,
        "consecutive_failures": 0,
        "last_alert_ts": None,
        "degraded_alerted_ts": None,
    }
    return {**base, **overrides}


def iso(dt):
    return dt.isoformat()


# --- restock alert rule ---


def test_first_observation_in_alerts():
    """null→in: first observation alerts (bias toward alerting)."""
    decision = decide(make_target_state(), "in", NOW)
    assert decision.restock_alert is True
    assert decision.target_state["state"] == "in"
    assert decision.target_state["last_known_stock_state"] == "in"
    assert decision.target_state["last_alert_ts"] == iso(NOW)


def test_out_to_in_alerts():
    previous = make_target_state(state="out", last_known_stock_state="out")
    decision = decide(previous, "in", NOW)
    assert decision.restock_alert is True
    assert decision.transition == "out→in"


def test_in_to_in_no_alert():
    previous = make_target_state(state="in", last_known_stock_state="in")
    decision = decide(previous, "in", NOW)
    assert decision.restock_alert is False
    assert decision.transition is None


def test_unknown_to_in_alerts_when_last_known_not_in():
    previous = make_target_state(
        state="unknown", last_known_stock_state="out", consecutive_failures=3
    )
    decision = decide(previous, "in", NOW)
    assert decision.restock_alert is True
    assert decision.transition == "unknown→in"


def test_in_unknown_in_no_alert():
    """in→unknown→in: unknown never overwrote last_known_stock_state, so no alert."""
    was_in = make_target_state(state="in", last_known_stock_state="in")
    after_unknown = decide(was_in, "unknown", NOW).target_state
    assert after_unknown["state"] == "unknown"
    assert after_unknown["last_known_stock_state"] == "in"
    back_in = decide(after_unknown, "in", NOW + timedelta(minutes=5))
    assert back_in.restock_alert is False


def test_unknown_preserves_last_known_stock_state():
    previous = make_target_state(state="out", last_known_stock_state="out")
    decision = decide(previous, "unknown", NOW)
    assert decision.target_state["last_known_stock_state"] == "out"
    assert decision.target_state["state"] == "unknown"
    assert decision.restock_alert is False


# --- flicker cap ---


def test_flicker_inside_one_hour_suppressed():
    previous = make_target_state(
        state="out",
        last_known_stock_state="out",
        last_alert_ts=iso(NOW - timedelta(minutes=30)),
    )
    decision = decide(previous, "in", NOW)
    assert decision.restock_alert is False
    assert decision.target_state["state"] == "in"  # state still updates
    assert decision.target_state["last_alert_ts"] == iso(NOW - timedelta(minutes=30))


def test_flicker_after_one_hour_alerts():
    previous = make_target_state(
        state="out",
        last_known_stock_state="out",
        last_alert_ts=iso(NOW - timedelta(hours=2)),
    )
    decision = decide(previous, "in", NOW)
    assert decision.restock_alert is True
    assert decision.target_state["last_alert_ts"] == iso(NOW)


def test_flicker_at_exactly_one_hour_alerts():
    previous = make_target_state(
        state="out",
        last_known_stock_state="out",
        last_alert_ts=iso(NOW - timedelta(seconds=3600)),
    )
    assert decide(previous, "in", NOW).restock_alert is True


# --- degraded-target rule ---


def test_eleven_failures_no_degraded_alert():
    previous = make_target_state(state="unknown", consecutive_failures=10)
    decision = decide(previous, "unknown", NOW)
    assert decision.target_state["consecutive_failures"] == 11
    assert decision.degraded_alert is False


def test_twelfth_failure_fires_degraded_alert():
    previous = make_target_state(state="unknown", consecutive_failures=11)
    decision = decide(previous, "unknown", NOW)
    assert decision.target_state["consecutive_failures"] == 12
    assert decision.degraded_alert is True
    assert decision.target_state["degraded_alerted_ts"] == iso(NOW)


def test_degraded_repeat_inside_24h_suppressed():
    previous = make_target_state(
        state="unknown",
        consecutive_failures=12,
        degraded_alerted_ts=iso(NOW - timedelta(hours=1)),
    )
    decision = decide(previous, "unknown", NOW)
    assert decision.degraded_alert is False
    assert decision.target_state["consecutive_failures"] == 13


def test_degraded_realerts_after_24h():
    previous = make_target_state(
        state="unknown",
        consecutive_failures=24,
        degraded_alerted_ts=iso(NOW - timedelta(hours=25)),
    )
    decision = decide(previous, "unknown", NOW)
    assert decision.degraded_alert is True
    assert decision.target_state["degraded_alerted_ts"] == iso(NOW)


def test_degraded_cooldown_boundary_at_exactly_24h():
    previous = make_target_state(
        state="unknown",
        consecutive_failures=20,
        degraded_alerted_ts=iso(NOW - timedelta(hours=24)),
    )
    assert decide(previous, "unknown", NOW).degraded_alert is True


def test_success_resets_failure_counter():
    previous = make_target_state(state="unknown", consecutive_failures=7)
    decision = decide(previous, "out", NOW)
    assert decision.target_state["consecutive_failures"] == 0
    assert decision.target_state["state"] == "out"


# --- hand-edited state.json robustness (acceptance tests edit it on the state branch) ---


def test_naive_stored_timestamp_biases_toward_alerting():
    """A hand-typed timestamp missing its UTC offset must never crash decide()."""
    previous = make_target_state(
        state="out",
        last_known_stock_state="out",
        last_alert_ts="2026-07-05T11:30:00",  # naive, 30 min before NOW
    )
    assert decide(previous, "in", NOW).restock_alert is True


def test_garbage_stored_timestamp_biases_toward_alerting():
    previous = make_target_state(
        state="out", last_known_stock_state="out", last_alert_ts="not a timestamp"
    )
    assert decide(previous, "in", NOW).restock_alert is True


# --- purity ---


def test_decide_never_mutates_input():
    previous = make_target_state(state="out", last_known_stock_state="out")
    frozen = json.dumps(previous, sort_keys=True)
    decide(previous, "in", NOW)
    decide(previous, "unknown", NOW)
    assert json.dumps(previous, sort_keys=True) == frozen


def test_decision_is_immutable():
    decision = decide(make_target_state(), "in", NOW)
    assert isinstance(decision, Decision)
    try:
        decision.restock_alert = False
        raised = False
    except AttributeError:
        raised = True
    assert raised


# --- state.json persistence ---


def test_load_absent_file_returns_first_run_state(tmp_path):
    state = load_state(tmp_path / "state.json")
    assert state == initial_state()
    assert state["schema_version"] == 1
    assert state["targets"] == {}


def test_save_load_roundtrip(tmp_path):
    path = tmp_path / ".state" / "state.json"
    state = initial_state()
    state = {
        **state,
        "last_run": iso(NOW),
        "targets": {"12k-cool": make_target_state(state="out", last_known_stock_state="out")},
    }
    save_state(path, state)
    assert load_state(path) == state


def test_load_malformed_file_returns_first_run_state(tmp_path):
    path = tmp_path / "state.json"
    path.write_text("{not json", encoding="utf-8")
    assert load_state(path) == initial_state()


def test_load_wrong_shape_returns_first_run_state(tmp_path):
    path = tmp_path / "state.json"
    path.write_text(json.dumps({"targets": "oops"}), encoding="utf-8")
    assert load_state(path) == initial_state()


def test_target_state_returns_default_for_unseen_target():
    state = initial_state()
    assert target_state(state, "12k-cool") == make_target_state()


def test_target_state_returns_copy():
    state = initial_state()
    first = target_state(state, "12k-cool")
    first["consecutive_failures"] = 99
    assert target_state(state, "12k-cool")["consecutive_failures"] == 0


def test_target_state_returns_stored_state():
    stored = make_target_state(state="in", last_known_stock_state="in")
    state = {**initial_state(), "targets": {"14k-heat": stored}}
    assert target_state(state, "14k-heat") == stored
