"""Entry-point tests: the Phase 4 run loop, --self-test flow, exit codes, arguments.

Run-loop invariants under test (SPEC.md Phase 4): one isolated fetch+decide per
target, alerts on both channels, daily heartbeat at the first run after 07:00 UTC,
state saved with an updated last_run every run, the DMS refreshed on a successful
run only, restock follow-up rounds strictly after state and DMS so they can never
delay either, and no secret value in anything the run prints.
"""

import json
import time
import urllib.error
import urllib.request
from datetime import UTC, datetime
from types import SimpleNamespace

import pytest

from watcher import shopify
from watcher.__main__ import main

# Not a real discord.com URL — keeps the SPEC acceptance grep for webhook URLs clean.
WEBHOOK_URL = "https://discord.example/api/webhooks/999/entrypoint-secret-token"
TOPIC = "entrypoint-secret-topic"

NTFY_URL = f"https://ntfy.sh/{TOPIC}"
DMS_URL = f"https://ntfy.sh/{TOPIC}/watcher-dead"

FIXED_NOW = datetime(2026, 7, 8, 12, 0, 0, tzinfo=UTC)
TODAY = "2026-07-08"
YESTERDAY = "2026-07-07"
PREVIOUS_RUN = "2026-07-08T11:55:00+00:00"


class FakeResponse:
    def __init__(self, status=200):
        self.status = status

    def __enter__(self):
        return self

    def __exit__(self, *exc_info):
        return False


@pytest.fixture
def sent(monkeypatch):
    """Set both channel secrets and capture every request urlopen would send."""
    monkeypatch.setenv("DISCORD_WEBHOOK_URL", WEBHOOK_URL)
    monkeypatch.setenv("NTFY_TOPIC", TOPIC)
    monkeypatch.delenv("DMS_DELAY", raising=False)
    requests = []

    def fake_urlopen(request, timeout=None):
        requests.append(request)
        return FakeResponse(204 if "discord" in request.full_url else 200)

    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)
    return requests


# --- run-loop harness ---


def write_targets(tmp_path, ids=("12k-cool", "14k-heat")):
    entries = [
        {
            "id": target_id,
            "retailer": "Example retailer",
            "model": f"Example {target_id}",
            "product_url": f"https://shop.example/products/{target_id}",
            "endpoint": f"https://shop.example/products/{target_id}.js",
            "variant_ids": [1],
        }
        for target_id in ids
    ]
    (tmp_path / "targets.json").write_text(json.dumps(entries), encoding="utf-8")


def target_state(**overrides):
    return {
        "state": "out",
        "last_known_stock_state": "out",
        "consecutive_failures": 0,
        "last_alert_ts": None,
        "degraded_alerted_ts": None,
        **overrides,
    }


def write_state(tmp_path, targets, last_heartbeat_date=TODAY, last_run=PREVIOUS_RUN):
    state = {
        "schema_version": 1,
        "last_run": last_run,
        "last_heartbeat_date": last_heartbeat_date,
        "targets": targets,
    }
    state_dir = tmp_path / ".state"
    state_dir.mkdir(exist_ok=True)
    (state_dir / "state.json").write_text(json.dumps(state), encoding="utf-8")


def saved_state(tmp_path):
    return json.loads((tmp_path / ".state" / "state.json").read_text(encoding="utf-8"))


@pytest.fixture
def loop(monkeypatch, tmp_path, sent):
    """Run-loop harness: tmp CWD, fixed clock, canned stock per target id."""
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("COMMIT_MESSAGE_FILE", raising=False)
    monkeypatch.setattr("watcher.__main__._utcnow", lambda: FIXED_NOW)
    stocks = {}
    monkeypatch.setattr(shopify, "fetch_target", lambda target: stocks[target.id])
    sleeps = []
    monkeypatch.setattr(time, "sleep", lambda seconds: sleeps.append(seconds))
    return SimpleNamespace(tmp_path=tmp_path, stocks=stocks, requests=sent, sleeps=sleeps)


def urls(requests):
    return [request.full_url for request in requests]


# --- run loop: quiet pass ---


def test_quiet_run_refreshes_dms_only_and_saves_last_run(loop, capsys):
    write_targets(loop.tmp_path)
    write_state(loop.tmp_path, {"12k-cool": target_state(), "14k-heat": target_state()})
    loop.stocks.update({"12k-cool": "out", "14k-heat": "out"})
    assert main([]) == 0
    assert urls(loop.requests) == [DMS_URL]
    assert loop.sleeps == []
    out = capsys.readouterr().out
    assert f"last_run={PREVIOUS_RUN}" in out
    assert "12k-cool: out" in out
    assert "14k-heat: out" in out
    state = saved_state(loop.tmp_path)
    assert state["last_run"] == FIXED_NOW.isoformat()
    assert state["targets"]["12k-cool"] == target_state()


# --- run loop: restock alerts ---


def test_restock_alerts_repeat_after_state_and_dms(loop, capsys):
    """One restock = three alert rounds a minute apart on both channels (a single
    4am ping is missable), but state save and the DMS refresh come first — the
    follow-up sleeps must never delay the audit trail or the liveness signal."""
    write_targets(loop.tmp_path)
    write_state(loop.tmp_path, {"12k-cool": target_state(), "14k-heat": target_state()})
    loop.stocks.update({"12k-cool": "out", "14k-heat": "in"})
    assert main([]) == 0
    assert urls(loop.requests) == [
        WEBHOOK_URL, NTFY_URL, DMS_URL,  # initial pass ends with the DMS refresh
        WEBHOOK_URL, NTFY_URL,  # follow-up round 1
        WEBHOOK_URL, NTFY_URL,  # follow-up round 2
    ]
    assert loop.sleeps == [60, 60]
    discord_payload = json.loads(loop.requests[0].data)
    assert "@everyone" in discord_payload["content"]
    assert discord_payload["embeds"][0]["title"] == "Example 14k-heat"
    assert loop.requests[1].get_header("Priority") == "urgent"
    assert "14k-heat: in out→in alert-sent" in capsys.readouterr().out
    state = saved_state(loop.tmp_path)["targets"]["14k-heat"]
    assert state["state"] == "in"
    assert state["last_known_stock_state"] == "in"
    assert state["last_alert_ts"] == FIXED_NOW.isoformat()


def test_first_run_bootstraps_state_alerts_and_heartbeats(loop):
    write_targets(loop.tmp_path)  # no .state directory at all
    loop.stocks.update({"12k-cool": "in", "14k-heat": "out"})
    assert main([]) == 0
    # first observation "in" alerts (null counts as not-in), first run heartbeats,
    # then the restock follow-up rounds
    assert urls(loop.requests) == [
        WEBHOOK_URL, NTFY_URL, WEBHOOK_URL, DMS_URL,
        WEBHOOK_URL, NTFY_URL,
        WEBHOOK_URL, NTFY_URL,
    ]
    heartbeat = json.loads(loop.requests[2].data)
    assert "@everyone" not in heartbeat["content"]
    state = saved_state(loop.tmp_path)
    assert state["targets"]["12k-cool"]["state"] == "in"
    assert state["targets"]["14k-heat"]["state"] == "out"
    assert state["last_heartbeat_date"] == TODAY


def test_bootstrap_branch_state_shape_is_tolerated(loop):
    """The orphan state branch starts as {"schema_version":1,"targets":{}} —
    missing last_run/last_heartbeat_date keys must behave as first-run state."""
    write_targets(loop.tmp_path)
    (loop.tmp_path / ".state").mkdir()
    (loop.tmp_path / ".state" / "state.json").write_text(
        '{"schema_version":1,"targets":{}}', encoding="utf-8"
    )
    loop.stocks.update({"12k-cool": "out", "14k-heat": "out"})
    assert main([]) == 0
    assert urls(loop.requests) == [WEBHOOK_URL, DMS_URL]  # heartbeat + DMS
    state = saved_state(loop.tmp_path)
    assert state["last_run"] == FIXED_NOW.isoformat()
    assert state["last_heartbeat_date"] == TODAY


# --- run loop: unknown and degraded ---


def test_unknown_fetch_never_alerts_and_preserves_last_known(loop):
    write_targets(loop.tmp_path)
    write_state(
        loop.tmp_path,
        {
            "12k-cool": target_state(state="in", last_known_stock_state="in"),
            "14k-heat": target_state(),
        },
    )
    loop.stocks.update({"12k-cool": "unknown", "14k-heat": "out"})
    assert main([]) == 0
    assert urls(loop.requests) == [DMS_URL]
    state = saved_state(loop.tmp_path)["targets"]["12k-cool"]
    assert state["state"] == "unknown"
    assert state["last_known_stock_state"] == "in"
    assert state["consecutive_failures"] == 1


def test_twelfth_consecutive_failure_sends_degraded_alerts(loop, monkeypatch, capsys):
    write_targets(loop.tmp_path)
    write_state(
        loop.tmp_path,
        {
            "12k-cool": target_state(state="unknown", consecutive_failures=11),
            "14k-heat": target_state(),
        },
    )
    loop.stocks.update({"12k-cool": "unknown", "14k-heat": "out"})
    message_file = loop.tmp_path / "commit-message.txt"
    monkeypatch.setenv("COMMIT_MESSAGE_FILE", str(message_file))
    assert main([]) == 0
    # degraded is a warning, not a restock: exactly one round, no follow-ups
    assert urls(loop.requests) == [WEBHOOK_URL, NTFY_URL, DMS_URL]
    assert loop.sleeps == []
    discord_payload = json.loads(loop.requests[0].data)
    assert "12k-cool" in discord_payload["content"]
    assert "@everyone" not in discord_payload["content"]
    assert loop.requests[1].get_header("Priority") is None  # degraded ntfy is default priority
    # a degraded alert must be visible in the run log and the state-branch audit trail,
    # even though unknown→unknown is not a transition
    assert "12k-cool: unknown degraded-alert-sent" in capsys.readouterr().out
    message = message_file.read_text(encoding="utf-8").strip()
    assert message == "run: 12k-cool DEGRADED; others unchanged"


def test_restock_still_reaches_ntfy_when_discord_is_down(loop, monkeypatch, capsys):
    """Channel independence in the run loop: a dead Discord webhook must not stop
    the ntfy alert, the state update, or the DMS refresh."""
    write_targets(loop.tmp_path)
    write_state(loop.tmp_path, {"12k-cool": target_state(), "14k-heat": target_state()})
    loop.stocks.update({"12k-cool": "out", "14k-heat": "in"})

    def discord_down(request, timeout=None):
        loop.requests.append(request)
        if "discord" in request.full_url:
            raise urllib.error.URLError("connection refused")
        return FakeResponse(200)

    monkeypatch.setattr(urllib.request, "urlopen", discord_down)
    assert main([]) == 0
    # follow-up rounds still fire (and keep trying Discord — they double as retries)
    assert urls(loop.requests) == [
        WEBHOOK_URL, NTFY_URL, DMS_URL,
        WEBHOOK_URL, NTFY_URL,
        WEBHOOK_URL, NTFY_URL,
    ]
    assert "14k-heat: in out→in alert-sent" in capsys.readouterr().out
    state = saved_state(loop.tmp_path)["targets"]["14k-heat"]
    assert state["last_alert_ts"] == FIXED_NOW.isoformat()


# --- run loop: daily heartbeat ---


def test_heartbeat_lists_every_target_without_mention(loop):
    write_targets(loop.tmp_path)
    write_state(
        loop.tmp_path,
        {"12k-cool": target_state(), "14k-heat": target_state()},
        last_heartbeat_date=YESTERDAY,
    )
    loop.stocks.update({"12k-cool": "out", "14k-heat": "out"})
    assert main([]) == 0
    assert urls(loop.requests) == [WEBHOOK_URL, DMS_URL]
    payload = json.loads(loop.requests[0].data)
    assert "12k-cool: out" in payload["content"]
    assert "14k-heat: out" in payload["content"]
    assert payload["allowed_mentions"] == {"parse": []}
    assert saved_state(loop.tmp_path)["last_heartbeat_date"] == TODAY


def test_no_heartbeat_before_seven_utc(loop, monkeypatch):
    monkeypatch.setattr(
        "watcher.__main__._utcnow", lambda: datetime(2026, 7, 8, 6, 59, 0, tzinfo=UTC)
    )
    write_targets(loop.tmp_path)
    write_state(
        loop.tmp_path,
        {"12k-cool": target_state(), "14k-heat": target_state()},
        last_heartbeat_date=YESTERDAY,
    )
    loop.stocks.update({"12k-cool": "out", "14k-heat": "out"})
    assert main([]) == 0
    assert urls(loop.requests) == [DMS_URL]
    assert saved_state(loop.tmp_path)["last_heartbeat_date"] == YESTERDAY


def test_future_heartbeat_date_still_heartbeats_today(loop):
    """SPEC expects hand-edits to state.json on the state branch; a fat-fingered
    future date must not silence the heartbeat for a month — only "already sent
    today" suppresses it."""
    write_targets(loop.tmp_path)
    write_state(
        loop.tmp_path,
        {"12k-cool": target_state(), "14k-heat": target_state()},
        last_heartbeat_date="2026-08-08",
    )
    loop.stocks.update({"12k-cool": "out", "14k-heat": "out"})
    assert main([]) == 0
    assert urls(loop.requests) == [WEBHOOK_URL, DMS_URL]
    assert saved_state(loop.tmp_path)["last_heartbeat_date"] == TODAY


def test_failed_heartbeat_send_retries_next_run(loop, monkeypatch):
    write_targets(loop.tmp_path)
    write_state(
        loop.tmp_path,
        {"12k-cool": target_state(), "14k-heat": target_state()},
        last_heartbeat_date=YESTERDAY,
    )
    loop.stocks.update({"12k-cool": "out", "14k-heat": "out"})

    def discord_down(request, timeout=None):
        loop.requests.append(request)
        if "discord" in request.full_url:
            raise urllib.error.URLError("connection refused")
        return FakeResponse(200)

    monkeypatch.setattr(urllib.request, "urlopen", discord_down)
    assert main([]) == 0
    # date not advanced, so the next run tries again instead of going silent for a day
    assert saved_state(loop.tmp_path)["last_heartbeat_date"] == YESTERDAY


# --- run loop: failure isolation and exit codes ---


def test_config_error_exits_nonzero_without_fetching_or_sending(loop, capsys):
    # no targets.json in the tmp CWD
    assert main([]) == 1
    assert loop.requests == []
    assert not (loop.tmp_path / ".state" / "state.json").exists()
    assert "config error" in capsys.readouterr().out


def test_one_crashing_target_does_not_stop_the_rest(loop, monkeypatch, capsys):
    write_targets(loop.tmp_path)
    write_state(loop.tmp_path, {"12k-cool": target_state(), "14k-heat": target_state()})

    def explode(target):
        if target.id == "12k-cool":
            raise RuntimeError(f"boom while posting to {WEBHOOK_URL}")
        return "out"

    monkeypatch.setattr(shopify, "fetch_target", explode)
    assert main([]) == 0
    assert urls(loop.requests) == [DMS_URL]
    assert loop.sleeps == []
    out = capsys.readouterr().out
    assert "14k-heat: out" in out
    assert WEBHOOK_URL not in out  # crash messages can embed URLs; log class name only
    # the crashed target's state is carried forward unchanged
    assert saved_state(loop.tmp_path)["targets"]["12k-cool"] == target_state()


def test_run_output_leaks_no_secret(loop, capsys):
    write_targets(loop.tmp_path)
    write_state(loop.tmp_path, {"12k-cool": target_state(), "14k-heat": target_state()})
    loop.stocks.update({"12k-cool": "in", "14k-heat": "out"})
    assert main([]) == 0
    combined = capsys.readouterr().out
    assert WEBHOOK_URL not in combined
    assert TOPIC not in combined


# --- run loop: commit message for the state branch ---


def test_commit_message_summarises_transitions_and_alerts(loop, monkeypatch):
    write_targets(loop.tmp_path)
    write_state(loop.tmp_path, {"12k-cool": target_state(), "14k-heat": target_state()})
    loop.stocks.update({"12k-cool": "out", "14k-heat": "in"})
    message_file = loop.tmp_path / "commit-message.txt"
    monkeypatch.setenv("COMMIT_MESSAGE_FILE", str(message_file))
    assert main([]) == 0
    message = message_file.read_text(encoding="utf-8").strip()
    assert message == "run: 14k-heat out→in ALERTED; others unchanged"


def test_commit_message_reports_a_quiet_run(loop, monkeypatch):
    write_targets(loop.tmp_path)
    write_state(loop.tmp_path, {"12k-cool": target_state(), "14k-heat": target_state()})
    loop.stocks.update({"12k-cool": "out", "14k-heat": "out"})
    message_file = loop.tmp_path / "commit-message.txt"
    monkeypatch.setenv("COMMIT_MESSAGE_FILE", str(message_file))
    assert main([]) == 0
    assert message_file.read_text(encoding="utf-8").strip() == "run: no state changes"


def test_no_commit_message_file_written_when_env_unset(loop):
    write_targets(loop.tmp_path)
    write_state(loop.tmp_path, {"12k-cool": target_state(), "14k-heat": target_state()})
    loop.stocks.update({"12k-cool": "out", "14k-heat": "out"})
    assert main([]) == 0
    assert not (loop.tmp_path / "commit-message.txt").exists()


# --- self-test (Phase 3 behaviour, unchanged) ---


def test_self_test_sends_alert_push_and_unrefreshed_two_minute_dms(sent):
    assert main(["--self-test"]) == 0
    assert [request.full_url for request in sent] == [
        WEBHOOK_URL,
        f"https://ntfy.sh/{TOPIC}",
        # Its own sequence ID: a real run's DMS refresh must not cancel the
        # deliberately unrefreshed 2-minute self-test switch.
        f"https://ntfy.sh/{TOPIC}/watcher-dead-selftest",
    ]
    assert sent[2].get_header("In") == "2m"


def test_self_test_is_clearly_labelled_not_a_restock(sent):
    main(["--self-test"])
    discord_payload = json.loads(sent[0].data)
    assert "[SELF-TEST]" in discord_payload["content"]
    assert "[SELF-TEST]" in sent[1].get_header("Title")


def test_self_test_returns_one_when_a_channel_is_unconfigured(monkeypatch):
    monkeypatch.setenv("DISCORD_WEBHOOK_URL", WEBHOOK_URL)
    monkeypatch.delenv("NTFY_TOPIC", raising=False)
    monkeypatch.setattr(
        urllib.request, "urlopen", lambda request, timeout=None: FakeResponse(204)
    )
    assert main(["--self-test"]) == 1


def test_self_test_reaches_ntfy_and_dms_when_discord_secret_is_malformed(monkeypatch, capsys):
    """Channel independence: a mispasted Discord secret (Request() raises before any
    network I/O) must not stop the ntfy push or the DMS schedule, and must not leak."""
    monkeypatch.setenv("DISCORD_WEBHOOK_URL", "mispasted-secret-token-xyz")
    monkeypatch.setenv("NTFY_TOPIC", TOPIC)
    requests = []

    def fake_urlopen(request, timeout=None):
        requests.append(request)
        return FakeResponse(200)

    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)
    assert main(["--self-test"]) == 1
    assert [request.full_url for request in requests] == [
        f"https://ntfy.sh/{TOPIC}",
        f"https://ntfy.sh/{TOPIC}/watcher-dead-selftest",
    ]
    captured = capsys.readouterr()
    assert "mispasted-secret-token-xyz" not in captured.out + captured.err


def test_self_test_output_leaks_no_secret(sent, capsys):
    main(["--self-test"])
    captured = capsys.readouterr()
    combined = captured.out + captured.err
    assert WEBHOOK_URL not in combined
    assert TOPIC not in combined


# --- argument handling ---


def _forbid_network(request, timeout=None):
    raise AssertionError("unknown arguments must not send anything")


def test_unknown_arguments_exit_2_without_sending(monkeypatch):
    monkeypatch.setattr(urllib.request, "urlopen", _forbid_network)
    assert main(["--bogus"]) == 2
