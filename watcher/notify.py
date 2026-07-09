"""Notification senders: Discord webhook (primary), ntfy (secondary + dead-man's-switch).

Every sender reads its env var at call time, swallows and logs its own errors, and
returns whether the send succeeded — one broken channel must never break another
(SPEC.md Phase 3). Secret hygiene: urllib exceptions can embed the request URL,
which contains the webhook secret or ntfy topic, so failures log only the
exception class name (plus the HTTP status code) — never str(exc) or the URL.
"""

import json
import os
import urllib.error
import urllib.request

from watcher.config import Target

TIMEOUT_S = 10
NTFY_BASE_URL = "https://ntfy.sh"
DMS_SEQUENCE_ID = "watcher-dead"
# The self-test's deliberately unrefreshed 2-minute switch needs its own sequence
# ID: on the shared one, any real run inside those 2 minutes would replace (cancel)
# it server-side and the self-test would look like a DMS failure.
DMS_SELF_TEST_SEQUENCE_ID = "watcher-dead-selftest"
DMS_DEFAULT_DELAY = "3h"


def discord_alert(target: Target) -> bool:
    """Sleep-breaking restock alert: @everyone mention plus a clickable embed."""
    payload = {
        "content": f"@everyone IN STOCK: {target.model}",
        "embeds": [
            {
                "title": target.model,
                "url": target.product_url,
                "description": f"Back in stock at {target.retailer} — go!",
            }
        ],
        "allowed_mentions": {"parse": ["everyone"]},
    }
    return _discord_post(payload, label="discord alert")


def discord_degraded(target: Target) -> bool:
    payload = {
        "content": (
            f"Degraded target: {target.id} ({target.model}) has been unreachable for "
            "~1 hour; its stock state is unknown. Check the Actions logs."
        ),
        "allowed_mentions": {"parse": []},
    }
    return _discord_post(payload, label="discord degraded")


def discord_heartbeat(summary: str) -> bool:
    payload = {
        "content": f"Daily heartbeat — watcher alive.\n{summary}",
        "allowed_mentions": {"parse": []},
    }
    return _discord_post(payload, label="discord heartbeat")


def ntfy_alert(target: Target) -> bool:
    headers = {
        "Priority": "urgent",
        "Click": target.product_url,
        "Tags": "rotating_light",
        "Title": f"IN STOCK: {target.model}",
    }
    body = f"{target.model} is back in stock at {target.retailer}\n{target.product_url}"
    return _ntfy_post("", body, headers, label="ntfy alert")


def ntfy_degraded(target: Target) -> bool:
    headers = {"Title": f"Watcher degraded: {target.id}"}
    body = (
        f"{target.id} ({target.model}) has been unreachable for ~1 hour; "
        "its stock state is unknown."
    )
    return _ntfy_post("", body, headers, label="ntfy degraded")


def refresh_dms(delay: str | None = None, *, sequence_id: str = DMS_SEQUENCE_ID) -> bool:
    """Re-schedule the dead-man's-switch; it only ever fires if runs stop refreshing it.

    Publishing to the same sequence ID replaces any pending message server-side.
    """
    delay = delay or os.environ.get("DMS_DELAY") or DMS_DEFAULT_DELAY
    headers = {
        "In": delay,
        "Priority": "high",
        "Title": "Cirro watcher has stopped running",
    }
    body = (
        f"No watcher run has refreshed this dead-man's-switch within {delay}. "
        "Check the repository's Actions tab."
    )
    return _ntfy_post(f"/{sequence_id}", body, headers, label="ntfy DMS refresh")


def _discord_post(payload: dict, label: str) -> bool:
    webhook_url = os.environ.get("DISCORD_WEBHOOK_URL")
    if not webhook_url:
        print(f"{label}: DISCORD_WEBHOOK_URL not set; skipping")
        return False
    body = json.dumps(payload).encode("utf-8")
    return _post(webhook_url, body, {"Content-Type": "application/json"}, label)


def _ntfy_post(path: str, body: str, headers: dict, label: str) -> bool:
    topic = os.environ.get("NTFY_TOPIC")
    if not topic:
        print(f"{label}: NTFY_TOPIC not set; skipping")
        return False
    # http.client encodes header values as Latin-1; replace anything outside it
    # (an em-dash in a product name) so one odd character can't kill the channel.
    safe_headers = {
        name: value.encode("latin-1", "replace").decode("latin-1")
        for name, value in headers.items()
    }
    return _post(f"{NTFY_BASE_URL}/{topic}{path}", body.encode("utf-8"), safe_headers, label)


def _post(url: str, body: bytes, headers: dict, label: str) -> bool:
    # Request() itself must sit inside the try: a mispasted env value with no URL
    # scheme raises ValueError whose message is the raw secret.
    try:
        request = urllib.request.Request(url, data=body, headers=headers, method="POST")
        with urllib.request.urlopen(request, timeout=TIMEOUT_S) as response:
            if not 200 <= response.status < 300:
                print(f"{label}: send failed (HTTP {response.status})")
                return False
    except urllib.error.HTTPError as exc:
        print(f"{label}: send failed (HTTP {exc.code})")
        return False
    except Exception as exc:
        print(f"{label}: send failed ({type(exc).__name__})")
        return False
    print(f"{label}: sent")
    return True
