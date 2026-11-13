# dbt-drift

Compares a dbt `manifest.json` against what actually exists in Snowflake and writes a
`.sql` file of `DROP` statements for objects dbt no longer manages.

**It never executes anything.** It only runs `SELECT`s against `INFORMATION_SCHEMA`
(tagged with `QUERY_TAG = 'dbt-drift (read-only scan)'`). The SQL file is for you to review and run yourself.

## Install

```bash
uv sync            # or: pip install -e .
```

## Usage

```bash
# 1. Build a manifest for the SAME target you're going to scan
dbt parse --target prod

# 2. Scan (run from the dbt project dir, or pass --project-dir)
dbt-drift --target prod -o drop_drift.sql
```

Credentials come from your existing `profiles.yml`, found the same way dbt finds it
(`--profiles-dir` > `$DBT_PROFILES_DIR` > `<project>/profiles.yml` > `~/.dbt/`).
Password, key-pair (`private_key_path` / `private_key`), `externalbrowser`, and OAuth auth all work,
and so do `env_var()` references.

### Options

| Flag | Purpose |
|---|---|
| `--target/-t`, `--profile`, `--profiles-dir`, `--project-dir` | Same meaning as in dbt |
| `--manifest PATH` | Defaults to `<project-dir>/target/manifest.json` |
| `-o/--output PATH` | SQL output file (default `drop_drift.sql`) |
| `--format table\|json` | Report on stdout |
| `--include-schema [DB.]SCHEMA` | Also scan a schema the manifest no longer references (e.g. all its models were deleted) |
| `--exclude GLOB` | Ignore matches. Patterns with dots match `DB.SCHEMA.NAME`, others match `NAME`. Case-insensitive |
| `--no-default-excludes` | Stop ignoring `*__DBT_TMP` / `*__DBT_BACKUP` |
| `--commented` | Write every `DROP` commented out, so you opt in line by line |
| `--max-manifest-age-hours N` | Warn on stale manifests (default 24, `0` disables) |

Exit codes: `0` no drift, `1` drift found, `2` error. Usable as a CI check.

## What counts as drift

| Category | Meaning | In SQL file? |
|---|---|---|
| Orphaned | In a scanned schema, not in the manifest (or its node is disabled) | `DROP` statement |
| Missing | In the manifest, not in Snowflake | No (report only) |
| Type mismatch | e.g. manifest says view, Snowflake has a table | Comment only |

**Scope:** only schemas that contain at least one manifest model, seed, or snapshot (plus
`--include-schema`). Objects declared as dbt **sources** are never dropped.

Drops are ordered views → materialized views → dynamic tables → external tables → tables,
and identifiers are fully qualified and quoted with Snowflake's actual casing.

## Caveats

- **The manifest must match the target.** Database and schema names are resolved at parse time. A
  manifest parsed against `dev` makes everything in `prod` look orphaned. The tool warns when
  the profile's database isn't in the manifest, or when orphans outnumber matched objects.
- Anything else writing into dbt-managed schemas (Fivetran, manual tables, another project)
  shows up as orphaned unless it's a declared source or `--exclude`d.
- Names are compared case-insensitively. Two objects differing only by quoted case are treated as one.
- Schemas are never dropped, even if they end up empty.

## Development

```bash
uv run pytest
```
