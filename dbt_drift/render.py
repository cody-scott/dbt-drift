"""Turn a DriftReport into a reviewable .sql file, a console table, or JSON."""

from __future__ import annotations

import json
from datetime import datetime, timezone

from .models import DriftItem, DriftReport, ManifestInfo, ObjectKind
from .snowflake import quote_ident

# Drop dependents before what they depend on: views/MVs/dynamic tables first, then tables.
DROP_ORDER = [
    ObjectKind.VIEW,
    ObjectKind.MATERIALIZED_VIEW,
    ObjectKind.DYNAMIC_TABLE,
    ObjectKind.EXTERNAL_TABLE,
    ObjectKind.TABLE,
]

DROP_KEYWORD = {
    ObjectKind.VIEW: "VIEW",
    ObjectKind.MATERIALIZED_VIEW: "MATERIALIZED VIEW",
    ObjectKind.DYNAMIC_TABLE: "DYNAMIC TABLE",
    ObjectKind.EXTERNAL_TABLE: "EXTERNAL TABLE",
    ObjectKind.TABLE: "TABLE",
}


def _qualified(item: DriftItem) -> str:
    r = item.relation
    return ".".join(quote_ident(p) for p in (r.database, r.schema, r.name))


def render_sql(
    report: DriftReport,
    manifest: ManifestInfo,
    target_label: str,
    commented: bool = False,
    now: datetime | None = None,
) -> str:
    now = now or datetime.now(timezone.utc)
    prefix = "-- " if commented else ""
    lines = [
        "-- =====================================================================",
        "-- dbt-drift: drop statements for objects NOT managed by the dbt manifest",
        "-- THIS FILE WAS NOT EXECUTED. Review every statement before running it.",
        "-- =====================================================================",
        f"-- generated:      {now.strftime('%Y-%m-%dT%H:%M:%SZ')}",
        f"-- target:         {target_label}",
        f"-- manifest:       {manifest.path}",
        f"--   project:      {manifest.project_name}",
        f"--   dbt version:  {manifest.dbt_version}",
        f"--   generated_at: {manifest.generated_at}",
        f"--   invocation:   {manifest.invocation_id}",
        f"-- schemas scanned: {len(report.scanned_schemas)}",
        f"-- orphaned objects: {len(report.orphaned)}",
    ]
    if commented:
        lines.append("-- Statements are commented out (--commented). Uncomment the ones you want to run.")
    lines.append("")

    if not report.orphaned:
        lines.append("-- No orphaned objects found. Nothing to drop.")
        return "\n".join(lines) + "\n"

    unknown = [i for i in report.orphaned if i.relation.kind not in DROP_KEYWORD]
    for kind in DROP_ORDER:
        items = [i for i in report.orphaned if i.relation.kind is kind]
        if not items:
            continue
        lines.append(f"-- ---- {DROP_KEYWORD[kind]}S ({len(items)}) ----")
        for item in items:
            lines.append(f"-- reason: {item.reason}")
            lines.append(f"{prefix}DROP {DROP_KEYWORD[kind]} IF EXISTS {_qualified(item)};")
        lines.append("")

    if unknown:
        lines.append("-- ---- UNRECOGNISED OBJECT TYPES: drop statement not generated ----")
        for item in unknown:
            lines.append(f"-- {_qualified(item)}  ({item.reason})")
        lines.append("")

    if report.type_mismatch:
        lines.append("-- ---- TYPE MISMATCHES (not dropped; dbt will usually replace on next run) ----")
        for item in report.type_mismatch:
            lines.append(f"-- {_qualified(item)}: {item.reason}")
        lines.append("")

    return "\n".join(lines)


def render_table(report: DriftReport) -> str:
    out: list[str] = []

    def section(title: str, items: list[DriftItem]) -> None:
        out.append(f"\n{title} ({len(items)})")
        out.append("-" * (len(title) + 6))
        if not items:
            out.append("  none")
            return
        w_name = max(len(i.relation.fqn) for i in items)
        w_kind = max(len(i.relation.kind.value) for i in items)
        for i in items:
            out.append(f"  {i.relation.fqn:<{w_name}}  {i.relation.kind.value:<{w_kind}}  {i.reason}")

    out.append(f"Scanned {len(report.scanned_schemas)} schema(s): " + ", ".join(f"{d}.{s}" for d, s in report.scanned_schemas))
    section("ORPHANED: in warehouse, not in manifest (will be dropped)", report.orphaned)
    section("MISSING: in manifest, not in warehouse", report.missing)
    section("TYPE MISMATCH", report.type_mismatch)
    if report.excluded:
        section("EXCLUDED by pattern", report.excluded)
    return "\n".join(out) + "\n"


def render_json(report: DriftReport, manifest: ManifestInfo, target_label: str) -> str:
    def items(lst: list[DriftItem]) -> list[dict]:
        return [
            {
                "database": i.relation.database,
                "schema": i.relation.schema,
                "name": i.relation.name,
                "kind": i.relation.kind.value,
                "reason": i.reason,
                "unique_id": i.expected.unique_id if i.expected else None,
            }
            for i in lst
        ]

    return json.dumps(
        {
            "target": target_label,
            "manifest": {
                "path": manifest.path,
                "project_name": manifest.project_name,
                "dbt_version": manifest.dbt_version,
                "generated_at": manifest.generated_at,
                "invocation_id": manifest.invocation_id,
            },
            "scanned_schemas": [f"{d}.{s}" for d, s in report.scanned_schemas],
            "orphaned": items(report.orphaned),
            "missing": items(report.missing),
            "type_mismatch": items(report.type_mismatch),
            "excluded": items(report.excluded),
        },
        indent=2,
    )
