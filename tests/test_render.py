import json
from datetime import datetime, timezone

from dbt_drift.compare import compare
from dbt_drift.manifest import load_manifest
from dbt_drift.models import ObjectKind as K
from dbt_drift.models import Relation
from dbt_drift.render import render_json, render_sql, render_table

NOW = datetime(2026, 11, 13, 21, 0, tzinfo=timezone.utc)


def _report(manifest_path, actual):
    m = load_manifest(manifest_path)
    return m, compare(m, actual, m.managed_schemas)


def test_sql_order_and_quoting(manifest_path):
    m, r = _report(
        manifest_path,
        [
            Relation("ANALYTICS", "MARTS", "OLD_TBL", K.TABLE),
            Relation("ANALYTICS", "MARTS", 'WEIRD"NAME', K.VIEW),
            Relation("ANALYTICS", "MARTS", "DYN", K.DYNAMIC_TABLE),
        ],
    )
    sql = render_sql(r, m, "p.prod", now=NOW)
    drops = [line for line in sql.splitlines() if line.startswith("DROP")]
    assert drops == [
        'DROP VIEW IF EXISTS "ANALYTICS"."MARTS"."WEIRD""NAME";',
        'DROP DYNAMIC TABLE IF EXISTS "ANALYTICS"."MARTS"."DYN";',
        'DROP TABLE IF EXISTS "ANALYTICS"."MARTS"."OLD_TBL";',
    ]
    assert "NOT EXECUTED" in sql
    assert "2026-11-13T21:00:00Z" in sql


def test_sql_commented(manifest_path):
    m, r = _report(manifest_path, [Relation("ANALYTICS", "MARTS", "OLD_TBL", K.TABLE)])
    sql = render_sql(r, m, "p.prod", commented=True, now=NOW)
    assert not any(line.startswith("DROP") for line in sql.splitlines())
    assert '-- DROP TABLE IF EXISTS "ANALYTICS"."MARTS"."OLD_TBL";' in sql


def test_sql_no_drift(manifest_path):
    m, r = _report(manifest_path, [])
    assert "Nothing to drop" in render_sql(r, m, "p.prod", now=NOW)


def test_unknown_kind_not_dropped(manifest_path):
    m, r = _report(manifest_path, [Relation("ANALYTICS", "MARTS", "ODD", K.UNKNOWN)])
    sql = render_sql(r, m, "p.prod", now=NOW)
    assert "DROP" not in "".join(line for line in sql.splitlines() if not line.startswith("--"))
    assert "UNRECOGNISED" in sql


def test_json_and_table(manifest_path):
    m, r = _report(manifest_path, [Relation("ANALYTICS", "MARTS", "OLD_TBL", K.TABLE)])
    data = json.loads(render_json(r, m, "p.prod"))
    assert data["orphaned"][0]["name"] == "OLD_TBL"
    assert "ORPHANED" in render_table(r)
