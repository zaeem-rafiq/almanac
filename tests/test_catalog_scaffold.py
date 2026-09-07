"""A-00 scaffold guards for facts/.

These assert the catalog is a well-formed, *unfilled* scaffold. The null-value assertion is
inverted in A-01 (where a null value becomes a failure) once Zaeem supplies the real numbers.
"""

import json
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
REQUIRED_KEYS = {
    "key", "label", "kind", "source", "value",
    "unit", "effective_from", "effective_to", "source_url", "max_age_days",
}


def _entities():
    data = yaml.safe_load((ROOT / "facts" / "catalog.yaml").read_text())
    return data["entities"]


def test_catalog_parses_and_has_entities():
    assert _entities(), "catalog.yaml must define at least one entity"


def test_every_entity_has_the_full_key_set():
    for entity in _entities():
        assert set(entity) == REQUIRED_KEYS, (
            f"{entity.get('key')!r} key set mismatch: "
            f"missing={REQUIRED_KEYS - set(entity)} extra={set(entity) - REQUIRED_KEYS}"
        )


def test_keys_are_unique():
    keys = [e["key"] for e in _entities()]
    assert len(keys) == len(set(keys)), f"duplicate keys: {keys}"


def test_source_is_manual_or_rates():
    for entity in _entities():
        assert entity["source"] in {"manual", "rates"}, (
            f"{entity['key']!r} has source={entity['source']!r}; only 'manual' or 'rates' allowed"
        )


def test_agent_filled_no_manual_value():
    """The agent may scaffold entries with value: null; it may not fill values."""
    for entity in _entities():
        if entity["source"] == "manual":
            assert entity["value"] is None, (
                f"{entity['key']!r} has a value the agent must not supply: {entity['value']!r}. "
                "Only Zaeem fills manual values, with a primary-source URL."
            )


def test_rates_json_is_an_empty_object():
    assert json.loads((ROOT / "facts" / "rates.json").read_text()) == {}
