"""python -m watcher entry point.

Phase 3 ships only --self-test; the full run loop arrives in Phase 4. A plain run
exits 2 so a no-op can never be mistaken for a successful watch.
"""

import sys

from watcher import notify
from watcher.config import Target

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


def self_test() -> int:
    """Prove both channels and the DMS end-to-end with real sends."""
    print(
        "self-test: sending one Discord alert and one urgent ntfy push, then "
        "scheduling a 2-minute dead-man's-switch that nothing will refresh"
    )
    results = (
        notify.discord_alert(_SELF_TEST_TARGET),
        notify.ntfy_alert(_SELF_TEST_TARGET),
        notify.refresh_dms("2m"),
    )
    if all(results):
        print("self-test: all sends succeeded — the watcher-dead message should fire in ~2 minutes")
        return 0
    print("self-test: one or more sends failed (see the lines above)")
    return 1


def main(argv: list[str]) -> int:
    if argv == ["--self-test"]:
        return self_test()
    print(
        "cirro-restock-watcher: the watch loop lands in Phase 4; "
        "only `python -m watcher --self-test` is available right now"
    )
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
