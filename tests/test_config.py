"""targets.json loading and validation — config errors must crash loudly."""

import json
from pathlib import Path

import pytest

from watcher.config import ConfigError, Target, load_targets

REPO_TARGETS = Path(__file__).parents[1] / "targets.json"


def valid_entry(**overrides):
    entry = {
        "id": "12k-cool",
        "retailer": "Meaco direct",
        "model": "Meaco Cirro 12000 BTU (cooling only)",
        "product_url": "https://meaco.com/products/meaco-cirro-12000",
        "endpoint": "https://meaco.com/products/meaco-cirro-12000.js",
        "variant_ids": [58102727246211],
    }
    return {**entry, **overrides}


def write_config(tmp_path, data):
    path = tmp_path / "targets.json"
    path.write_text(json.dumps(data), encoding="utf-8")
    return path


# Every store verified Shopify with public /products/<handle>.js (probed 2026-07-10,
# all variants showing available=false while the range was genuinely sold out).
STORE_BY_RETAILER = {
    "Meaco direct": "https://meaco.com/products/",
    "Aircare Appliances": "https://aircareappliances.co.uk/products/",
    "Air Con Centre": "https://www.airconcentre.co.uk/products/",
}


def test_repo_targets_json_is_valid():
    targets = load_targets(REPO_TARGETS)
    assert [target.id for target in targets] == [
        "12k-cool", "14k-cool", "14k-heat",
        "aircare-12k-cool", "aircare-14k-cool", "aircare-14k-heat",
        "airconcentre-12k-cool", "airconcentre-14k-cool", "airconcentre-14k-heat",
    ]
    for target in targets:
        assert isinstance(target, Target)
        assert target.endpoint.startswith(STORE_BY_RETAILER[target.retailer])
        assert target.endpoint == target.product_url + ".js"
        assert all(isinstance(variant_id, int) for variant_id in target.variant_ids)
        assert len(target.variant_ids) == 1


def test_variant_ids_load_as_immutable_tuple(tmp_path):
    (target,) = load_targets(write_config(tmp_path, [valid_entry()]))
    assert target.variant_ids == (58102727246211,)
    assert isinstance(target.variant_ids, tuple)


def test_missing_file_raises(tmp_path):
    with pytest.raises(ConfigError):
        load_targets(tmp_path / "nope.json")


def test_invalid_json_raises(tmp_path):
    path = tmp_path / "targets.json"
    path.write_text("{not json", encoding="utf-8")
    with pytest.raises(ConfigError):
        load_targets(path)


def test_non_array_raises(tmp_path):
    with pytest.raises(ConfigError):
        load_targets(write_config(tmp_path, {"id": "x"}))


def test_empty_array_raises(tmp_path):
    with pytest.raises(ConfigError):
        load_targets(write_config(tmp_path, []))


@pytest.mark.parametrize("field", ["id", "retailer", "model", "product_url", "endpoint"])
def test_missing_required_field_raises(tmp_path, field):
    entry = valid_entry()
    del entry[field]
    with pytest.raises(ConfigError):
        load_targets(write_config(tmp_path, [entry]))


def test_blank_id_raises(tmp_path):
    with pytest.raises(ConfigError):
        load_targets(write_config(tmp_path, [valid_entry(id="  ")]))


def test_duplicate_ids_raise(tmp_path):
    with pytest.raises(ConfigError):
        load_targets(write_config(tmp_path, [valid_entry(), valid_entry()]))


def test_non_https_endpoint_raises(tmp_path):
    entry = valid_entry(endpoint="http://meaco.com/products/x.js")
    with pytest.raises(ConfigError):
        load_targets(write_config(tmp_path, [entry]))


def test_endpoint_not_dot_js_raises(tmp_path):
    entry = valid_entry(endpoint="https://meaco.com/products/x")
    with pytest.raises(ConfigError):
        load_targets(write_config(tmp_path, [entry]))


def test_variant_ids_missing_raises(tmp_path):
    entry = valid_entry()
    del entry["variant_ids"]
    with pytest.raises(ConfigError):
        load_targets(write_config(tmp_path, [entry]))


def test_variant_ids_not_integers_raises(tmp_path):
    entry = valid_entry(variant_ids=["58102727246211"])
    with pytest.raises(ConfigError):
        load_targets(write_config(tmp_path, [entry]))


def test_variant_ids_booleans_rejected(tmp_path):
    entry = valid_entry(variant_ids=[True])
    with pytest.raises(ConfigError):
        load_targets(write_config(tmp_path, [entry]))


def test_string_fields_are_stripped(tmp_path):
    entry = valid_entry(id=" 12k-cool ", retailer=" Meaco direct ")
    (target,) = load_targets(write_config(tmp_path, [entry]))
    assert target.id == "12k-cool"
    assert target.retailer == "Meaco direct"


def test_empty_variant_ids_allowed(tmp_path):
    (target,) = load_targets(write_config(tmp_path, [valid_entry(variant_ids=[])]))
    assert target.variant_ids == ()
