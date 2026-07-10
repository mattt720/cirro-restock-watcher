"""Empirical endpoint probe (SPEC.md Phase 1, now targets.json-driven): can GitHub
runners read every watched store's Shopify endpoint?

Fetches each targets.json endpoint with the production User-Agent, prints the evidence
(HTTP status, product title, per-variant id/title/available), and saves each raw JSON
payload to --out (uploaded as workflow artifacts; they become tests/fixtures/).
Exits non-zero if any fetch fails, so the probe workflow goes red.

Run on Actions (the only verdict that counts — runner egress IPs differ from home):
the `probe` workflow via workflow_dispatch. Run locally: python scripts/probe.py
(meaco.com fails TLS verification on some stale local trust stores; Actions verifies).
"""

import argparse
import json
import sys
import urllib.request
from pathlib import Path

USER_AGENT = "cirro-restock-watcher/1.0 (+https://github.com/mattt720/cirro-restock-watcher)"
TIMEOUT_S = 10
MAX_BYTES = 1_000_000
TARGETS_PATH = Path(__file__).parents[1] / "targets.json"


def fetch(url: str) -> bytes:
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(request, timeout=TIMEOUT_S) as response:
        if response.status != 200:
            raise RuntimeError(f"HTTP {response.status}")
        return response.read(MAX_BYTES)


def print_egress_ip() -> None:
    try:
        ip = fetch("https://api.ipify.org").decode("ascii", "replace")
        print(f"runner egress IP: {ip}")
    except Exception as exc:  # diagnostic only — never fails the probe
        print(f"runner egress IP: lookup failed ({exc})")


def probe_target(target_id: str, retailer: str, url: str, out_dir: Path) -> bool:
    print(f"\n== {target_id} ({retailer}) ==\n   {url}")
    try:
        raw = fetch(url)
        product = json.loads(raw)
        variants = product["variants"]
    except Exception as exc:
        print(f"   FAIL: {type(exc).__name__}: {exc}")
        return False
    out_dir.joinpath(f"{target_id}.json").write_bytes(raw)
    print(f"   HTTP 200: {product.get('title', '<no title>')}")
    for variant in variants:
        print(
            f"   variant {variant.get('id')}: {variant.get('title')!r} "
            f"available={variant.get('available')}"
        )
    return True


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", default="probe-output", help="directory for raw JSON payloads")
    args = parser.parse_args()
    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)

    targets = json.loads(TARGETS_PATH.read_text(encoding="utf-8"))
    print_egress_ip()
    results = {
        t["id"]: probe_target(t["id"], t["retailer"], t["endpoint"], out_dir) for t in targets
    }

    print("\n== summary ==")
    for target_id, ok in results.items():
        print(f"   {target_id}: {'OK' if ok else 'FAIL'}")
    return 0 if all(results.values()) else 1


if __name__ == "__main__":
    sys.exit(main())
