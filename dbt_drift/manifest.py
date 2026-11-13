"""Parse a dbt manifest.json into the set of relations dbt expects to exist."""

from __future__ import annotations

import json
from pathlib import Path

from .models import ExpectedRelation, ManifestInfo, ObjectKind, Relation

# resource types that create a physical object in the warehouse
MANAGED_RESOURCE_TYPES = {"model", "seed", "snapshot"}

MATERIALIZATION_KINDS = {
    "view": ObjectKind.VIEW,
    "table": ObjectKind.TABLE,
    "incremental": ObjectKind.TABLE,
    "snapshot": ObjectKind.TABLE,
    "seed": ObjectKind.TABLE,
    "materialized_view": ObjectKind.MATERIALIZED_VIEW,
    "dynamic_table": ObjectKind.DYNAMIC_TABLE,
}

# materializations that never produce an object
NON_PHYSICAL = {"ephemeral"}


class ManifestError(Exception):
    pass


def _node_to_expected(node: dict) -> ExpectedRelation | None:
    resource_type = node.get("resource_type")
    if resource_type not in MANAGED_RESOURCE_TYPES:
        return None

    config = node.get("config") or {}
    materialized = config.get("materialized") or (
        "seed" if resource_type == "seed" else "snapshot" if resource_type == "snapshot" else "unknown"
    )
    if materialized in NON_PHYSICAL:
        return None

    database = node.get("database")
    schema = node.get("schema")
    name = node.get("alias") or node.get("name")
    if not (database and schema and name):
        # Disabled nodes in particular may not have a resolved location.
        return None

    kind = MATERIALIZATION_KINDS.get(materialized, ObjectKind.UNKNOWN)
    return ExpectedRelation(
        relation=Relation(database, schema, name, kind),
        unique_id=node.get("unique_id", "?"),
        resource_type=resource_type,
        materialized=materialized,
    )


def load_manifest(path: str | Path) -> ManifestInfo:
    path = Path(path)
    if not path.exists():
        raise ManifestError(f"manifest not found: {path} (run `dbt parse --target <target>` first)")
    try:
        data = json.loads(path.read_text())
    except json.JSONDecodeError as exc:
        raise ManifestError(f"could not parse {path}: {exc}") from exc

    if "nodes" not in data or "metadata" not in data:
        raise ManifestError(f"{path} does not look like a dbt manifest (missing nodes/metadata)")

    meta = data.get("metadata") or {}
    info = ManifestInfo(
        path=str(path),
        dbt_version=meta.get("dbt_version"),
        generated_at=meta.get("generated_at"),
        invocation_id=meta.get("invocation_id"),
        project_name=meta.get("project_name"),
    )

    for node in data["nodes"].values():
        exp = _node_to_expected(node)
        if exp:
            info.expected.append(exp)

    # `disabled` maps unique_id -> list of node variants
    for variants in (data.get("disabled") or {}).values():
        for node in variants:
            exp = _node_to_expected(node)
            if exp:
                info.disabled.append(exp)

    for src in (data.get("sources") or {}).values():
        database, schema = src.get("database"), src.get("schema")
        name = src.get("identifier") or src.get("name")
        if database and schema and name:
            info.sources.append(Relation(database, schema, name))

    return info
