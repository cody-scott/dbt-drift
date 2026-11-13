import pytest

from dbt_drift.manifest import ManifestError, load_manifest
from dbt_drift.models import ObjectKind


def test_parses_physical_nodes_only(manifest_path):
    m = load_manifest(manifest_path)
    names = {e.relation.name for e in m.expected}
    assert names == {"stg_orders", "fct_orders", "customers", "country_codes", "orders_snap"}
    assert "int_helper" not in names  # ephemeral
    assert m.project_name == "jaffle"


def test_kinds(manifest_path):
    m = load_manifest(manifest_path)
    kinds = {e.relation.name: e.relation.kind for e in m.expected}
    assert kinds["stg_orders"] is ObjectKind.VIEW
    assert kinds["fct_orders"] is ObjectKind.TABLE
    assert kinds["country_codes"] is ObjectKind.TABLE
    assert kinds["orders_snap"] is ObjectKind.TABLE


def test_disabled_and_sources(manifest_path):
    m = load_manifest(manifest_path)
    assert [d.relation.name for d in m.disabled] == ["old_model"]
    assert [s.name for s in m.sources] == ["raw_orders"]


def test_managed_schemas(manifest_path):
    m = load_manifest(manifest_path)
    assert m.managed_schemas == {
        ("ANALYTICS", "STAGING"),
        ("ANALYTICS", "MARTS"),
        ("ANALYTICS", "SEEDS"),
        ("ANALYTICS", "SNAPSHOTS"),
    }


def test_missing_file(tmp_path):
    with pytest.raises(ManifestError, match="dbt parse"):
        load_manifest(tmp_path / "nope.json")


def test_not_a_manifest(tmp_path):
    p = tmp_path / "m.json"
    p.write_text('{"foo": 1}')
    with pytest.raises(ManifestError, match="does not look like"):
        load_manifest(p)
