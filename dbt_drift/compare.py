"""Pure comparison logic: expected (manifest) vs actual (warehouse). No I/O here."""

from __future__ import annotations

import fnmatch
from collections.abc import Iterable

from .models import DriftItem, DriftReport, ManifestInfo, ObjectKind, Relation

# Leftovers from interrupted dbt runs. These are matched against the bare object name.
DEFAULT_EXCLUDES = ["*__DBT_TMP", "*__DBT_BACKUP"]


def is_excluded(rel: Relation, patterns: Iterable[str]) -> str | None:
    """Return the matching pattern if `rel` is excluded.

    Patterns are case-insensitive globs. A pattern containing dots is matched
    against DATABASE.SCHEMA.NAME; otherwise against the bare object name.
    """
    fqn = rel.fqn.upper()
    name = rel.name.upper()
    for pat in patterns:
        p = pat.upper()
        target = fqn if "." in p else name
        if fnmatch.fnmatchcase(target, p):
            return pat
    return None


def compare(
    manifest: ManifestInfo,
    actual: Iterable[Relation],
    scanned_schemas: Iterable[tuple[str, str]],
    exclude_patterns: Iterable[str] = (),
) -> DriftReport:
    exclude_patterns = list(exclude_patterns)
    scanned = {(d.upper(), s.upper()) for d, s in scanned_schemas}

    expected_by_key = {e.relation.key: e for e in manifest.expected}
    disabled_by_key = {e.relation.key: e for e in manifest.disabled if e.relation.key not in expected_by_key}
    source_keys = {s.key for s in manifest.sources}

    report = DriftReport(scanned_schemas=sorted(scanned))
    actual_by_key: dict[tuple[str, str, str], Relation] = {}

    for rel in actual:
        if rel.schema_key not in scanned:
            continue
        actual_by_key[rel.key] = rel

        exp = expected_by_key.get(rel.key)
        if exp is not None:
            ek = exp.relation.kind
            if ek is not ObjectKind.UNKNOWN and rel.kind is not ObjectKind.UNKNOWN and ek is not rel.kind:
                report.type_mismatch.append(
                    DriftItem(rel, f"manifest expects {ek.value} ({exp.materialized}), found {rel.kind.value}", exp)
                )
            continue

        if rel.key in source_keys:
            continue  # declared as a dbt source: not ours to drop

        pat = is_excluded(rel, exclude_patterns)
        if pat:
            report.excluded.append(DriftItem(rel, f"matches exclude pattern '{pat}'"))
            continue

        if rel.key in disabled_by_key:
            d = disabled_by_key[rel.key]
            report.orphaned.append(DriftItem(rel, f"node is disabled ({d.unique_id})", d))
        else:
            report.orphaned.append(DriftItem(rel, "not in manifest"))

    for key, exp in expected_by_key.items():
        if exp.relation.schema_key in scanned and key not in actual_by_key:
            report.missing.append(DriftItem(exp.relation, f"{exp.unique_id} not built in warehouse", exp))

    for lst in (report.orphaned, report.missing, report.type_mismatch, report.excluded):
        lst.sort(key=lambda i: i.relation.key)
    return report
