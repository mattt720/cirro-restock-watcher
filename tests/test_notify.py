"""Notification sender tests: payload shapes, channel independence, secret hygiene.

Invariants under test (SPEC.md Phase 3): every sender swallows its own errors and
returns False instead of raising, and no secret value (webhook URL, ntfy topic)
ever appears in anything the module prints.
"""

import io
import json
import urllib.error
import urllib.request
from dataclasses import replace

import pytest

from watcher.config import USER_AGENT, Target
from watcher.notify import (
    DMS_SELF_TEST_SEQUENCE_ID,
    discord_alert,
    discord_degraded,
    discord_heartbeat,
    ntfy_alert,
    ntfy_degraded,
    refresh_dms,
)

# Not a real discord.com URL: the SPEC acceptance grep for committed webhook URLs
# must stay clean, and the senders never inspect the URL anyway.
WEBHOOK_URL = "https://discord.example/api/webhooks/1234567890/secret-webhook-token"
TOPIC = "secret-cirro-topic-x7q2"

TARGET = Target(
    id="14k-heat",
    retailer="Meaco direct",
    model="Meaco Cirro+ 14000 BTU (cooling + heating)",
    product_url="https://meaco.com/products/meaco-cirro-14000-heater",
    endpoint="https://meaco.com/products/meaco-cirro-14000-heater.js",
    variant_ids=(58103896637827,),
)

SENDERS = [
    pytest.param(lambda: discord_alert(TARGET), id="discord_alert"),
    pytest.param(lambda: discord_degraded(TARGET), id="discord_degraded"),
    pytest.param(lambda: discord_heartbeat("all targets: out"), id="discord_heartbeat"),
    pytest.param(lambda: ntfy_alert(TARGET), id="ntfy_alert"),
    pytest.param(lambda: ntfy_degraded(TARGET), id="ntfy_degraded"),
    pytest.param(lambda: refresh_dms(), id="refresh_dms"),
]
DISCORD_SENDERS = SENDERS[:3]
NTFY_SENDERS = SENDERS[3:]

# Each failure factory embeds the request URL in the exception the way urllib
# really does, so the secret-hygiene assertions bite.
FAILURES = [
    pytest.param(
        lambda url: urllib.error.HTTPError(url, 500, "Internal Server Error", {}, io.BytesIO(b"")),
        id="http_500",
    ),
    pytest.param(lambda url: urllib.error.URLError(f"tunnel to {url} refused"), id="url_error"),
    pytest.param(lambda url: TimeoutError(f"timed out connecting to {url}"), id="timeout"),
]


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
    calls = []

    def fake_urlopen(request, timeout=None):
        calls.append({"request": request, "timeout": timeout})
        return FakeResponse(204 if "discord" in request.full_url else 200)

    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)
    return calls


# --- Discord payload shapes ---


def test_discord_alert_mentions_everyone_with_embed(sent):
    assert discord_alert(TARGET) is True
    [call] = sent
    request = call["request"]
    assert request.full_url == WEBHOOK_URL
    assert request.get_method() == "POST"
    assert request.get_header("Content-type") == "application/json"
    assert call["timeout"] == 10
    payload = json.loads(request.data)
    assert "@everyone" in payload["content"]
    assert "IN STOCK" in payload["content"]
    assert payload["allowed_mentions"] == {"parse": ["everyone"]}
    [embed] = payload["embeds"]
    assert embed["title"] == TARGET.model
    assert embed["url"] == TARGET.product_url
    assert TARGET.retailer in embed["description"]


def test_discord_degraded_names_target_without_mention(sent):
    assert discord_degraded(TARGET) is True
    [call] = sent
    payload = json.loads(call["request"].data)
    assert TARGET.id in payload["content"]
    assert "@everyone" not in payload["content"]
    assert payload["allowed_mentions"] == {"parse": []}


def test_discord_heartbeat_posts_summary_without_mention(sent):
    assert discord_heartbeat("12k-cool: out\n14k-cool: out") is True
    [call] = sent
    payload = json.loads(call["request"].data)
    assert "12k-cool: out" in payload["content"]
    assert "@everyone" not in payload["content"]
    assert payload["allowed_mentions"] == {"parse": []}


# --- ntfy payload shapes ---


def test_ntfy_alert_is_urgent_with_click_url(sent):
    assert ntfy_alert(TARGET) is True
    [call] = sent
    request = call["request"]
    assert request.full_url == f"https://ntfy.sh/{TOPIC}"
    assert request.get_method() == "POST"
    assert call["timeout"] == 10
    assert request.get_header("Priority") == "urgent"
    assert request.get_header("Click") == TARGET.product_url
    assert request.get_header("Tags") == "rotating_light"
    assert request.get_header("Title") == f"IN STOCK: {TARGET.model}"
    body = request.data.decode("utf-8")
    assert TARGET.model in body
    assert TARGET.product_url in body


def test_ntfy_degraded_uses_default_priority(sent):
    assert ntfy_degraded(TARGET) is True
    [call] = sent
    request = call["request"]
    assert request.full_url == f"https://ntfy.sh/{TOPIC}"
    assert request.get_header("Priority") is None
    assert TARGET.id in request.data.decode("utf-8")


# --- dead-man's-switch ---


def test_refresh_dms_defaults_to_three_hours(sent):
    assert refresh_dms() is True
    [call] = sent
    request = call["request"]
    assert request.full_url == f"https://ntfy.sh/{TOPIC}/watcher-dead"
    assert request.get_method() == "POST"
    assert request.get_header("In") == "3h"
    assert request.get_header("Priority") == "high"


def test_refresh_dms_delay_env_overrides_default(sent, monkeypatch):
    monkeypatch.setenv("DMS_DELAY", "6h")
    assert refresh_dms() is True
    assert sent[0]["request"].get_header("In") == "6h"


def test_refresh_dms_explicit_delay_beats_env(sent, monkeypatch):
    monkeypatch.setenv("DMS_DELAY", "6h")
    assert refresh_dms("2m") is True
    assert sent[0]["request"].get_header("In") == "2m"


def test_refresh_dms_self_test_sequence_id_cannot_collide_with_production(sent):
    """The self-test's unrefreshed 2-minute DMS must use its own sequence ID —
    on the shared ID, any real run inside those 2 minutes would silently replace
    (cancel) it, and the operator would wrongly conclude the DMS is broken."""
    assert refresh_dms("2m", sequence_id=DMS_SELF_TEST_SEQUENCE_ID) is True
    request = sent[0]["request"]
    assert request.full_url == f"https://ntfy.sh/{TOPIC}/{DMS_SELF_TEST_SEQUENCE_ID}"
    assert DMS_SELF_TEST_SEQUENCE_ID != "watcher-dead"


@pytest.mark.parametrize("send", SENDERS)
def test_every_send_carries_project_user_agent(send, sent):
    """Discord sits behind Cloudflare, which 403-bans urllib's default
    Python-urllib/x.y signature (error 1010) — observed live 2026-07-10 on the
    first real self-test. Every sender must identify itself explicitly."""
    assert send() is True
    assert sent[0]["request"].get_header("User-agent") == USER_AGENT


# --- failures return False, never raise, never leak a secret ---


@pytest.mark.parametrize("make_exc", FAILURES)
@pytest.mark.parametrize("send", SENDERS)
def test_send_failure_returns_false_and_leaks_no_secret(send, make_exc, monkeypatch, capsys):
    monkeypatch.setenv("DISCORD_WEBHOOK_URL", WEBHOOK_URL)
    monkeypatch.setenv("NTFY_TOPIC", TOPIC)

    def raise_exc(request, timeout=None):
        raise make_exc(request.full_url)

    monkeypatch.setattr(urllib.request, "urlopen", raise_exc)
    assert send() is False
    captured = capsys.readouterr()
    combined = captured.out + captured.err
    assert "failed" in combined
    assert WEBHOOK_URL not in combined
    assert "secret-webhook-token" not in combined
    assert TOPIC not in combined


@pytest.mark.parametrize("send", SENDERS)
def test_success_output_leaks_no_secret(send, sent, capsys):
    assert send() is True
    captured = capsys.readouterr()
    combined = captured.out + captured.err
    assert WEBHOOK_URL not in combined
    assert TOPIC not in combined


def test_malformed_webhook_url_fails_safely_without_leaking(monkeypatch, capsys):
    """A mispasted secret has no URL scheme, so Request() itself raises ValueError —
    that must be swallowed like any send failure, and the message (the raw secret)
    must never be printed."""
    monkeypatch.setenv("DISCORD_WEBHOOK_URL", "mispasted-token-abc123secret")
    calls = []

    def fake_urlopen(request, timeout=None):
        calls.append(request)
        return FakeResponse(204)

    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)
    assert discord_alert(TARGET) is False
    assert calls == []
    combined = capsys.readouterr().out
    assert "failed" in combined
    assert "abc123secret" not in combined


def test_ntfy_alert_survives_non_latin1_model_name(sent):
    """http.client encodes header values as Latin-1; an em-dash in a product name
    must degrade the Title, not kill the channel (body stays exact UTF-8)."""
    target = replace(TARGET, model="Meaco Cirro+ 14k — Heat/Cool")
    assert ntfy_alert(target) is True
    [call] = sent
    assert call["request"].get_header("Title").startswith("IN STOCK: Meaco Cirro+ 14k")
    # The mock bypasses http.client, so prove every header survives its Latin-1 step.
    for _, value in call["request"].header_items():
        value.encode("latin-1")
    assert "—" in call["request"].data.decode("utf-8")


def test_unexpected_response_status_is_failure(monkeypatch, capsys):
    monkeypatch.setenv("DISCORD_WEBHOOK_URL", WEBHOOK_URL)
    monkeypatch.setattr(urllib.request, "urlopen", lambda request, timeout=None: FakeResponse(302))
    assert discord_alert(TARGET) is False
    combined = capsys.readouterr().out
    assert "failed" in combined
    assert WEBHOOK_URL not in combined


# --- unconfigured channels skip cleanly (channels stay independent) ---


def _forbid_network(request, timeout=None):
    raise AssertionError("no request may be sent when the channel is unconfigured")


# Actions sets a referenced-but-undefined secret to "" rather than leaving it unset,
# so both forms must skip identically.
MISSING = ["absent", "empty"]


@pytest.mark.parametrize("missing", MISSING)
@pytest.mark.parametrize("send", DISCORD_SENDERS)
def test_discord_skips_without_webhook_url(send, missing, monkeypatch, capsys):
    if missing == "empty":
        monkeypatch.setenv("DISCORD_WEBHOOK_URL", "")
    else:
        monkeypatch.delenv("DISCORD_WEBHOOK_URL", raising=False)
    monkeypatch.setenv("NTFY_TOPIC", TOPIC)
    monkeypatch.setattr(urllib.request, "urlopen", _forbid_network)
    assert send() is False
    out = capsys.readouterr().out
    assert "DISCORD_WEBHOOK_URL not set" in out
    assert TOPIC not in out


@pytest.mark.parametrize("missing", MISSING)
@pytest.mark.parametrize("send", NTFY_SENDERS)
def test_ntfy_skips_without_topic(send, missing, monkeypatch, capsys):
    if missing == "empty":
        monkeypatch.setenv("NTFY_TOPIC", "")
    else:
        monkeypatch.delenv("NTFY_TOPIC", raising=False)
    monkeypatch.setenv("DISCORD_WEBHOOK_URL", WEBHOOK_URL)
    monkeypatch.setattr(urllib.request, "urlopen", _forbid_network)
    assert send() is False
    out = capsys.readouterr().out
    assert "NTFY_TOPIC not set" in out
    assert WEBHOOK_URL not in out
