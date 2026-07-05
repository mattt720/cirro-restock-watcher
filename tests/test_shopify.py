"""Stock derivation tests against the real Phase 1 fixtures plus failure modes.

Invariant under test: a failed or ambiguous fetch is "unknown", never "out".
"""

import io
import json
import urllib.error
import urllib.request
from pathlib import Path

import pytest

from watcher.config import Target
from watcher.shopify import derive_stock, fetch_target

FIXTURES = Path(__file__).parent / "fixtures"

REAL_FIXTURES = [
    ("12k-cool.json", 58102727246211),
    ("14k-cool.json", 58103889953155),
    ("14k-heat.json", 58103896637827),
]

TARGET = Target(
    id="12k-cool",
    retailer="Meaco direct",
    model="Meaco Cirro 12000 BTU (cooling only)",
    product_url="https://meaco.com/products/meaco-cirro-12000",
    endpoint="https://meaco.com/products/meaco-cirro-12000.js",
    variant_ids=(58102727246211,),
)


class FakeResponse:
    def __init__(self, status, body):
        self.status = status
        self.body = body
        self.read_sizes = []

    def __enter__(self):
        return self

    def __exit__(self, *exc_info):
        return False

    def read(self, size=-1):
        self.read_sizes.append(size)
        return self.body if size in (-1, None) else self.body[: size]


# --- derivation from real fixtures ---


@pytest.mark.parametrize(("fixture", "variant_id"), REAL_FIXTURES)
def test_real_fixture_sold_out_derives_out(fixture, variant_id):
    raw = (FIXTURES / fixture).read_bytes()
    assert derive_stock(raw, (variant_id,)) == "out"


def test_fixture_with_available_true_derives_in():
    product = json.loads((FIXTURES / "12k-cool.json").read_bytes())
    flipped = {
        **product,
        "variants": [
            {**variant, "available": True} if variant["id"] == 58102727246211 else variant
            for variant in product["variants"]
        ],
    }
    assert derive_stock(json.dumps(flipped).encode(), (58102727246211,)) == "in"


def test_empty_variant_ids_watches_any_variant():
    raw = (FIXTURES / "14k-cool.json").read_bytes()
    assert derive_stock(raw, ()) == "out"
    product = json.loads(raw)
    flipped = {
        **product,
        "variants": [{**variant, "available": True} for variant in product["variants"]],
    }
    assert derive_stock(json.dumps(flipped).encode(), ()) == "in"


# --- ambiguous payloads are unknown, never out ---


def test_malformed_json_is_unknown():
    assert derive_stock(b"<html>challenge page</html>", (58102727246211,)) == "unknown"


def test_truncated_json_is_unknown():
    raw = (FIXTURES / "12k-cool.json").read_bytes()
    assert derive_stock(raw[: len(raw) // 2], (58102727246211,)) == "unknown"


def test_non_object_json_is_unknown():
    assert derive_stock(b"[1, 2, 3]", (58102727246211,)) == "unknown"


def test_missing_variants_field_is_unknown():
    assert derive_stock(b'{"title": "Cirro"}', (58102727246211,)) == "unknown"


def test_variants_not_a_list_is_unknown():
    assert derive_stock(b'{"variants": "sold out"}', (58102727246211,)) == "unknown"


def test_variant_entries_not_objects_is_unknown():
    assert derive_stock(b'{"variants": [1, 2]}', (58102727246211,)) == "unknown"


def test_watched_variant_missing_from_payload_is_unknown():
    raw = (FIXTURES / "12k-cool.json").read_bytes()
    assert derive_stock(raw, (999,)) == "unknown"


def test_empty_variants_list_is_unknown():
    assert derive_stock(b'{"variants": []}', ()) == "unknown"


def test_unhashable_variant_id_is_unknown_not_crash():
    raw = b'{"variants": [{"id": [1, 2], "available": true}]}'
    assert derive_stock(raw, (1,)) == "unknown"


def test_watched_variant_missing_available_field_is_unknown():
    raw = b'{"variants": [{"id": 1}]}'
    assert derive_stock(raw, (1,)) == "unknown"


def test_watched_variant_non_boolean_available_is_unknown():
    raw = b'{"variants": [{"id": 1, "available": "false"}]}'
    assert derive_stock(raw, (1,)) == "unknown"


def test_unrelated_malformed_variant_does_not_block_derivation():
    raw = b'{"variants": [{"id": [1, 2], "available": true}, {"id": 5, "available": false}]}'
    assert derive_stock(raw, (5,)) == "out"


def test_fetch_target_never_raises_on_hostile_payload(monkeypatch):
    hostile = b'{"variants": [{"id": {"x": 1}, "available": true}]}'
    monkeypatch.setattr(
        urllib.request, "urlopen", lambda request, timeout=None: FakeResponse(200, hostile)
    )
    assert fetch_target(TARGET) == "unknown"


# --- fetch failure modes are unknown, never out ---


def test_http_500_is_unknown(monkeypatch):
    def raise_500(request, timeout=None):
        raise urllib.error.HTTPError(
            TARGET.endpoint, 500, "Internal Server Error", {}, io.BytesIO(b"")
        )

    monkeypatch.setattr(urllib.request, "urlopen", raise_500)
    assert fetch_target(TARGET) == "unknown"


def test_http_404_is_unknown(monkeypatch):
    def raise_404(request, timeout=None):
        raise urllib.error.HTTPError(TARGET.endpoint, 404, "Not Found", {}, io.BytesIO(b""))

    monkeypatch.setattr(urllib.request, "urlopen", raise_404)
    assert fetch_target(TARGET) == "unknown"


def test_timeout_is_unknown(monkeypatch):
    def raise_timeout(request, timeout=None):
        raise TimeoutError("timed out")

    monkeypatch.setattr(urllib.request, "urlopen", raise_timeout)
    assert fetch_target(TARGET) == "unknown"


def test_connection_failure_is_unknown(monkeypatch):
    def raise_url_error(request, timeout=None):
        raise urllib.error.URLError("connection refused")

    monkeypatch.setattr(urllib.request, "urlopen", raise_url_error)
    assert fetch_target(TARGET) == "unknown"


def test_non_200_status_is_unknown(monkeypatch):
    monkeypatch.setattr(
        urllib.request, "urlopen", lambda request, timeout=None: FakeResponse(204, b"{}")
    )
    assert fetch_target(TARGET) == "unknown"


def test_redirect_to_html_is_unknown(monkeypatch):
    """urllib follows redirects; a 200 HTML page must fail JSON parsing into unknown."""
    monkeypatch.setattr(
        urllib.request,
        "urlopen",
        lambda request, timeout=None: FakeResponse(200, b"<!doctype html><title>Login</title>"),
    )
    assert fetch_target(TARGET) == "unknown"


# --- successful fetch path ---


def test_fetch_success_derives_from_payload(monkeypatch):
    seen = {}
    raw = (FIXTURES / "12k-cool.json").read_bytes()

    def fake_urlopen(request, timeout=None):
        seen["url"] = request.full_url
        seen["user_agent"] = request.get_header("User-agent")
        seen["timeout"] = timeout
        return FakeResponse(200, raw)

    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)
    assert fetch_target(TARGET) == "out"
    assert seen["url"] == TARGET.endpoint
    assert seen["user_agent"] == (
        "cirro-restock-watcher/1.0 (+https://github.com/mattt720/cirro-restock-watcher)"
    )
    assert seen["timeout"] == 10


def test_fetch_reads_at_most_one_megabyte(monkeypatch):
    response = FakeResponse(200, (FIXTURES / "12k-cool.json").read_bytes())
    monkeypatch.setattr(urllib.request, "urlopen", lambda request, timeout=None: response)
    fetch_target(TARGET)
    assert response.read_sizes == [1_000_000]
