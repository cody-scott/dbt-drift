"""dbt-drift command line entrypoint.

Exit codes: 0 = no drift, 1 = drift found, 2 = error.
"""

from __future__ import annotations

import argparse
import sys
from datetime import datetime, timezone
from pathlib import Path

from . import __version__
from .compare import DEFAULT_EXCLUDES, compare
from .manifest import ManifestError, load_manifest
from .models import DriftReport, ManifestInfo
from .profiles import ProfileError, resolve_target
from .render import render_json, render_sql, render_table

EXIT_OK, EXIT_DRIFT, EXIT_ERROR = 0, 1, 2


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="dbt-drift",
        description=(
            "Compare a dbt manifest with Snowflake and write a .sql file that drops "
            "objects dbt no longer manages. Nothing is ever executed against the warehouse."
        ),
    )
    p.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    p.add_argument("--project-dir", default=".", help="dbt project directory (default: .)")
    p.add_argument("--manifest", help="path to manifest.json (default: <project-dir>/target/manifest.json)")
    p.add_argument("--profiles-dir", help="directory containing profiles.yml (default: dbt's lookup rules)")
    p.add_argument("--profile", help="profile name (default: from dbt_project.yml)")
    p.add_argument("--target", "-t", help="profile target (default: the profile's default target)")
    p.add_argument("--output", "-o", default="drop_drift.sql", help="where to write the drop SQL (default: drop_drift.sql)")
    p.add_argument("--format", choices=["table", "json"], default="table", help="report format on stdout")
    p.add_argument(
        "--include-schema",
        action="append",
        default=[],
        metavar="[DB.]SCHEMA",
        help="extra schema to scan, e.g. one whose models were all deleted (repeatable; DB defaults to the target database)",
    )
    p.add_argument(
        "--exclude",
        action="append",
        default=[],
        metavar="GLOB",
        help="ignore objects matching a case-insensitive glob; with dots it matches DB.SCHEMA.NAME, otherwise NAME (repeatable)",
    )
    p.add_argument("--no-default-excludes", action="store_true", help=f"don't ignore {', '.join(DEFAULT_EXCLUDES)}")
    p.add_argument("--commented", action="store_true", help="write every DROP statement commented out")
    p.add_argument(
        "--max-manifest-age-hours",
        type=float,
        default=24,
        help="warn if the manifest is older than this (0 disables; default 24)",
    )
    return p


def _warn(msg: str) -> None:
    print(f"WARNING: {msg}", file=sys.stderr)


def _check_manifest_age(manifest: ManifestInfo, max_hours: float) -> None:
    if not max_hours or not manifest.generated_at:
        return
    try:
        ts = datetime.fromisoformat(manifest.generated_at.replace("Z", "+00:00"))
    except ValueError:
        return
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=timezone.utc)
    age_h = (datetime.now(timezone.utc) - ts).total_seconds() / 3600
    if age_h > max_hours:
        _warn(
            f"manifest was generated {age_h:.1f}h ago ({manifest.generated_at}). "
            "Re-run `dbt parse --target <target>` so recently added models aren't reported as orphaned."
        )


def _parse_schema_arg(value: str, default_db: str | None) -> tuple[str, str]:
    parts = value.split(".")
    if len(parts) == 2:
        return parts[0], parts[1]
    if len(parts) == 1 and default_db:
        return default_db, parts[0]
    raise ValueError(f"--include-schema '{value}' must be SCHEMA or DB.SCHEMA")


def _sanity_check_report(report: DriftReport, manifest: ManifestInfo, target_db: str | None) -> None:
    dbs = {e.relation.database.upper() for e in manifest.expected}
    if target_db and dbs and target_db.upper() not in dbs:
        _warn(
            f"profile target database '{target_db}' is not used by any model in the manifest "
            f"(manifest databases: {', '.join(sorted(dbs))}). Was the manifest built for a different target?"
        )
    matched = len(manifest.expected) - len(report.missing)
    if len(report.orphaned) >= 5 and len(report.orphaned) > matched:
        _warn(
            f"{len(report.orphaned)} orphaned vs {matched} matched objects. This usually means the manifest "
            "was compiled for a different target than the one scanned. Check before using the SQL."
        )


def run(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    project_dir = Path(args.project_dir).expanduser()
    manifest_path = Path(args.manifest) if args.manifest else project_dir / "target" / "manifest.json"

    try:
        manifest = load_manifest(manifest_path)
        target = resolve_target(project_dir, args.profiles_dir, args.profile, args.target)
    except (ManifestError, ProfileError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return EXIT_ERROR

    _check_manifest_age(manifest, args.max_manifest_age_hours)
    if not manifest.expected:
        print("error: manifest contains no models/seeds/snapshots that create objects; refusing to continue", file=sys.stderr)
        return EXIT_ERROR

    target_db = target.config.get("database")
    try:
        extra = [_parse_schema_arg(s, target_db) for s in args.include_schema]
    except ValueError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return EXIT_ERROR
    schemas = sorted(manifest.managed_schemas | {(d.upper(), s.upper()) for d, s in extra})

    target_label = f"{target.profile_name}.{target.target_name} (account {target.config.get('account')})"
    print(f"Scanning {len(schemas)} schema(s) on {target_label} (read-only)...", file=sys.stderr)

    from .snowflake import connect, list_relations  # deferred: keeps --help fast

    try:
        conn = connect(target.config)
        try:
            actual = list_relations(conn, schemas)
        finally:
            conn.close()
    except Exception as exc:  # connector raises many types; surface them all cleanly
        print(f"error: Snowflake query failed: {exc}", file=sys.stderr)
        return EXIT_ERROR

    excludes = list(args.exclude) + ([] if args.no_default_excludes else DEFAULT_EXCLUDES)
    report = compare(manifest, actual, schemas, excludes)
    _sanity_check_report(report, manifest, target_db)

    out_path = Path(args.output)
    out_path.write_text(render_sql(report, manifest, target_label, commented=args.commented))

    if args.format == "json":
        print(render_json(report, manifest, target_label))
    else:
        print(render_table(report))
    print(f"Wrote {len(report.orphaned)} DROP statement(s) to {out_path} (not executed).", file=sys.stderr)

    return EXIT_DRIFT if report.has_drift else EXIT_OK


def main() -> None:
    sys.exit(run())


if __name__ == "__main__":
    main()
