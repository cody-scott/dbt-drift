import json
from pathlib import Path

import pytest


def _node(uid, rtype, db, schema, name, materialized, alias=None):
    return {
        "unique_id": uid,
        "resource_type": rtype,
        "database": db,
        "schema": schema,
        "name": name,
        "alias": alias or name,
        "config": {"materialized": materialized, "enabled": True},
    }


@pytest.fixture
def manifest_dict():
    return {
        "metadata": {
            "dbt_version": "1.9.0",
            "generated_at": "2026-11-13T12:00:00Z",
            "invocation_id": "abc-123",
            "project_name": "jaffle",
        },
        "nodes": {
            "model.jaffle.stg_orders": _node("model.jaffle.stg_orders", "model", "analytics", "staging", "stg_orders", "view"),
            "model.jaffle.fct_orders": _node("model.jaffle.fct_orders", "model", "analytics", "marts", "fct_orders", "incremental"),
            # alias differs from name: the alias is the real object name
            "model.jaffle.dim_customers": _node(
                "model.jaffle.dim_customers", "model", "analytics", "marts", "dim_customers", "table", alias="customers"
            ),
            "model.jaffle.int_helper": _node("model.jaffle.int_helper", "model", "analytics", "staging", "int_helper", "ephemeral"),
            "seed.jaffle.country_codes": _node("seed.jaffle.country_codes", "seed", "analytics", "seeds", "country_codes", "seed"),
            "snapshot.jaffle.orders_snap": _node(
                "snapshot.jaffle.orders_snap", "snapshot", "analytics", "snapshots", "orders_snap", "snapshot"
            ),
            "test.jaffle.not_null_x": {"unique_id": "test.jaffle.not_null_x", "resource_type": "test", "config": {}},
        },
        "disabled": {
            "model.jaffle.old_model": [
                _node("model.jaffle.old_model", "model", "analytics", "marts", "old_model", "table")
            ]
        },
        "sources": {
            "source.jaffle.raw.orders": {
                "database": "analytics",
                "schema": "staging",
                "name": "orders",
                "identifier": "raw_orders",
            }
        },
    }


@pytest.fixture
def manifest_path(tmp_path: Path, manifest_dict) -> Path:
    p = tmp_path / "manifest.json"
    p.write_text(json.dumps(manifest_dict))
    return p
