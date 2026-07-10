"""Fetch a target's Shopify /products/<handle>.js endpoint and derive stock state.

Invariant (SPEC.md): a fetch is successful only if it returns HTTP 200 with valid
JSON containing a variants list. Everything else — timeout, non-200, redirect to
HTML, malformed JSON, missing fields — is "unknown", never "out". Retailer JSON is
untrusted input: stdlib json only, reads capped at 1 MB.
"""

import json
import urllib.request
from typing import Literal

from watcher.config import USER_AGENT, Target

Stock = Literal["in", "out", "unknown"]

TIMEOUT_S = 10
MAX_BYTES = 1_000_000


def fetch_target(target: Target) -> Stock:
    try:
        raw = _fetch_raw(target.endpoint)
        stock = derive_stock(raw, target.variant_ids)
    except Exception as exc:
        # str(exc) is safe here, unlike in notify.py: the only URL it can embed is
        # target.endpoint, which is world-readable config, never a secret.
        print(f"{target.id}: fetch failed ({type(exc).__name__}: {exc})")
        return "unknown"
    if stock == "unknown":
        print(f"{target.id}: response was not product JSON containing the watched variants")
    return stock


def derive_stock(raw: bytes, variant_ids: tuple[int, ...]) -> Stock:
    """Pure derivation from a raw response body — no I/O, fixture-testable."""
    try:
        product = json.loads(raw)
    except (json.JSONDecodeError, UnicodeDecodeError):
        return "unknown"
    if not isinstance(product, dict):
        return "unknown"
    variants = product.get("variants")
    if not isinstance(variants, list) or not all(isinstance(v, dict) for v in variants):
        return "unknown"
    # Tuple membership compares by equality (no hashing), so a hostile non-hashable
    # variant "id" can never raise here.
    watched = [v for v in variants if v.get("id") in variant_ids] if variant_ids else variants
    availability = [v.get("available") for v in watched]
    if not availability or not all(isinstance(flag, bool) for flag in availability):
        return "unknown"  # watched variants absent or availability ambiguous is never "out"
    return "in" if any(availability) else "out"


def _fetch_raw(url: str) -> bytes:
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(request, timeout=TIMEOUT_S) as response:
        if response.status != 200:
            raise RuntimeError(f"HTTP {response.status}")
        return response.read(MAX_BYTES)
