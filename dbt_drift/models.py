"""Core data types shared across the tool."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum


class ObjectKind(str, Enum):
    TABLE = "TABLE"
    VIEW = "VIEW"
    MATERIALIZED_VIEW = "MATERIALIZED VIEW"
    DYNAMIC_TABLE = "DYNAMIC TABLE"
    EXTERNAL_TABLE = "EXTERNAL TABLE"
    UNKNOWN = "UNKNOWN"


@dataclass(frozen=True)
class Relation:
    """A fully qualified database object.

    `database`/`schema`/`name` keep the original spelling (needed to emit exact
    DDL); comparisons go through `key`, which is case-insensitive. Snowflake
    upper-cases unquoted identifiers, while dbt manifests usually hold
    lower-case names, so an exact comparison would flag everything.
    """

    database: str
    schema: str
    name: str
    kind: ObjectKind = ObjectKind.UNKNOWN

    @property
    def key(self) -> tuple[str, str, str]:
        return (self.database.upper(), self.schema.upper(), self.name.upper())

    @property
    def schema_key(self) -> tuple[str, str]:
        return (self.database.upper(), self.schema.upper())

    @property
    def fqn(self) -> str:
        return f"{self.database}.{self.schema}.{self.name}"


@dataclass(frozen=True)
class ExpectedRelation:
    """An object the dbt manifest says should exist."""

    relation: Relation
    unique_id: str
    resource_type: str
    materialized: str


@dataclass
class ManifestInfo:
    path: str
    dbt_version: str | None
    generated_at: str | None
    invocation_id: str | None
    project_name: str | None
    expected: list[ExpectedRelation] = field(default_factory=list)
    # Relations belonging to disabled nodes; if found in the warehouse they are drift.
    disabled: list[ExpectedRelation] = field(default_factory=list)
    # Sources are never dropped, even though they live in "managed" schemas.
    sources: list[Relation] = field(default_factory=list)

    @property
    def managed_schemas(self) -> set[tuple[str, str]]:
        return {e.relation.schema_key for e in self.expected}


@dataclass(frozen=True)
class DriftItem:
    relation: Relation
    reason: str
    expected: ExpectedRelation | None = None


@dataclass
class DriftReport:
    orphaned: list[DriftItem] = field(default_factory=list)
    missing: list[DriftItem] = field(default_factory=list)
    type_mismatch: list[DriftItem] = field(default_factory=list)
    excluded: list[DriftItem] = field(default_factory=list)
    scanned_schemas: list[tuple[str, str]] = field(default_factory=list)

    @property
    def has_drift(self) -> bool:
        return bool(self.orphaned or self.missing or self.type_mismatch)
