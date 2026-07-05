"""Load and validate targets.json.

Config errors are the one thing that *should* crash the run (SPEC.md Phase 2),
so every problem raises ConfigError with a message naming the offending entry.
"""

import json
from dataclasses import dataclass
from pathlib import Path

_REQUIRED_STR_FIELDS = ("id", "retailer", "model", "product_url", "endpoint")


class ConfigError(Exception):
    """targets.json is missing or malformed."""


@dataclass(frozen=True)
class Target:
    id: str
    retailer: str
    model: str
    product_url: str
    endpoint: str
    variant_ids: tuple[int, ...]


def load_targets(path: str | Path = "targets.json") -> tuple[Target, ...]:
    path = Path(path)
    try:
        raw = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise ConfigError(f"cannot read {path}: {exc}") from exc
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ConfigError(f"{path} is not valid JSON: {exc}") from exc
    if not isinstance(data, list) or not data:
        raise ConfigError(f"{path} must be a non-empty JSON array of targets")

    targets = tuple(_parse_target(entry, index) for index, entry in enumerate(data))
    ids = [target.id for target in targets]
    duplicates = sorted({target_id for target_id in ids if ids.count(target_id) > 1})
    if duplicates:
        raise ConfigError(f"duplicate target ids: {duplicates}")
    return targets


def _parse_target(entry: object, index: int) -> Target:
    if not isinstance(entry, dict):
        raise ConfigError(f"target #{index} must be a JSON object")
    fields = {}
    for field in _REQUIRED_STR_FIELDS:
        value = entry.get(field)
        if not isinstance(value, str) or not value.strip():
            raise ConfigError(f"target #{index}: {field!r} must be a non-empty string")
        fields[field] = value.strip()
    for field in ("product_url", "endpoint"):
        if not fields[field].startswith("https://"):
            raise ConfigError(f"target {fields['id']!r}: {field!r} must be an https:// URL")
    if not fields["endpoint"].endswith(".js"):
        raise ConfigError(
            f"target {fields['id']!r}: 'endpoint' must be a Shopify /products/<handle>.js URL"
        )
    variant_ids = entry.get("variant_ids")
    if not isinstance(variant_ids, list) or not all(
        isinstance(variant_id, int) and not isinstance(variant_id, bool)
        for variant_id in variant_ids
    ):
        raise ConfigError(f"target {fields['id']!r}: 'variant_ids' must be a list of integers")
    return Target(**fields, variant_ids=tuple(variant_ids))
