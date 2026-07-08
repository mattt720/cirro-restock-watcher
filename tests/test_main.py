"""Entry-point tests: --self-test flow, exit codes, argument handling."""

import json
import urllib.request

import pytest

from watcher.__main__ import main

# Not a real discord.com URL — keeps the SPEC acceptance grep for webhook URLs clean.
WEBHOOK_URL = "https://discord.example/api/webhooks/999/entrypoint-secret-token"
TOPIC = "entrypoint-secret-topic"


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


def test_self_test_sends_alert_push_and_unrefreshed_two_minute_dms(sent):
    assert main(["--self-test"]) == 0
    assert [request.full_url for request in sent] == [
        WEBHOOK_URL,
        f"https://ntfy.sh/{TOPIC}",
        f"https://ntfy.sh/{TOPIC}/watcher-dead",
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
        f"https://ntfy.sh/{TOPIC}/watcher-dead",
    ]
    captured = capsys.readouterr()
    assert "mispasted-secret-token-xyz" not in captured.out + captured.err


def test_self_test_output_leaks_no_secret(sent, capsys):
    main(["--self-test"])
    captured = capsys.readouterr()
    combined = captured.out + captured.err
    assert WEBHOOK_URL not in combined
    assert TOPIC not in combined


def _forbid_network(request, timeout=None):
    raise AssertionError("a plain run must not send anything before Phase 4")


def test_run_without_flags_exits_2_without_sending(monkeypatch, capsys):
    monkeypatch.setattr(urllib.request, "urlopen", _forbid_network)
    assert main([]) == 2
    assert "Phase 4" in capsys.readouterr().out


def test_unknown_arguments_exit_2_without_sending(monkeypatch):
    monkeypatch.setattr(urllib.request, "urlopen", _forbid_network)
    assert main(["--bogus"]) == 2
