from dbt_drift.compare import DEFAULT_EXCLUDES, compare, is_excluded
from dbt_drift.manifest import load_manifest
from dbt_drift.models import ObjectKind as K
from dbt_drift.models import Relation

SCHEMAS = [("ANALYTICS", "STAGING"), ("ANALYTICS", "MARTS"), ("ANALYTICS", "SEEDS"), ("ANALYTICS", "SNAPSHOTS")]


def warehouse():
    # Snowflake returns upper-case names
    return [
        Relation("ANALYTICS", "STAGING", "STG_ORDERS", K.VIEW),
        Relation("ANALYTICS", "STAGING", "RAW_ORDERS", K.TABLE),  # a dbt source
        Relation("ANALYTICS", "STAGING", "STG_LEGACY", K.VIEW),  # orphan
        Relation("ANALYTICS", "MARTS", "FCT_ORDERS", K.TABLE),
        Relation("ANALYTICS", "MARTS", "CUSTOMERS", K.VIEW),  # expected TABLE -> mismatch
        Relation("ANALYTICS", "MARTS", "OLD_MODEL", K.TABLE),  # disabled node
        Relation("ANALYTICS", "MARTS", "FCT_ORDERS__DBT_TMP", K.TABLE),  # default-excluded
        Relation("ANALYTICS", "SEEDS", "COUNTRY_CODES", K.TABLE),
        Relation("ANALYTICS", "OTHER", "NOT_OURS", K.TABLE),  # unscanned schema
        # snapshot ORDERS_SNAP is missing
    ]


def test_full_compare(manifest_path):
    m = load_manifest(manifest_path)
    r = compare(m, warehouse(), SCHEMAS, DEFAULT_EXCLUDES)

    assert [(i.relation.name, i.reason) for i in r.orphaned] == [
        ("OLD_MODEL", "node is disabled (model.jaffle.old_model)"),
        ("STG_LEGACY", "not in manifest"),
    ]
    assert [i.relation.name for i in r.missing] == ["orders_snap"]
    assert [i.relation.name for i in r.type_mismatch] == ["CUSTOMERS"]
    assert [i.relation.name for i in r.excluded] == ["FCT_ORDERS__DBT_TMP"]
    assert r.has_drift


def test_case_insensitive_match(manifest_path):
    m = load_manifest(manifest_path)
    actual = [Relation("analytics", "Staging", "stg_ORDERS", K.VIEW)]
    r = compare(m, actual, [("ANALYTICS", "STAGING")])
    assert not r.orphaned and not r.type_mismatch


def test_sources_never_dropped(manifest_path):
    m = load_manifest(manifest_path)
    r = compare(m, warehouse(), SCHEMAS)
    assert "RAW_ORDERS" not in {i.relation.name for i in r.orphaned}


def test_unscanned_schema_ignored(manifest_path):
    m = load_manifest(manifest_path)
    r = compare(m, warehouse(), SCHEMAS)
    assert "NOT_OURS" not in {i.relation.name for i in r.orphaned}


def test_missing_only_reported_for_scanned_schemas(manifest_path):
    m = load_manifest(manifest_path)
    r = compare(m, [], [("ANALYTICS", "SEEDS")])
    assert [i.relation.name for i in r.missing] == ["country_codes"]


def test_no_drift(manifest_path):
    m = load_manifest(manifest_path)
    actual = [Relation(e.relation.database, e.relation.schema, e.relation.name, e.relation.kind) for e in m.expected]
    r = compare(m, actual, SCHEMAS)
    assert not r.has_drift


def test_exclude_patterns():
    rel = Relation("ANALYTICS", "MARTS", "TMP_THING", K.TABLE)
    assert is_excluded(rel, ["tmp_*"]) == "tmp_*"
    assert is_excluded(rel, ["analytics.marts.*"]) == "analytics.marts.*"
    assert is_excluded(rel, ["analytics.staging.*"]) is None
    assert is_excluded(rel, ["*_THING_X"]) is None
