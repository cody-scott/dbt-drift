"""End-to-end CLI test with a fake Snowflake connection."""

import json

import pytest

from dbt_drift import cli, snowflake


class FakeCursor:
    def __init__(self, rows, executed):
        self.rows, self.executed = rows, executed

    def execute(self, sql, params=None):
        self.executed.append((sql, params))
        sql_upper = sql.upper()
        # Only reads are allowed.
        assert sql_upper.lstrip().startswith("SELECT"), sql
        for word in ("DROP", "DELETE", "ALTER", "CREATE", "TRUNCATE", "INSERT", "UPDATE"):
            assert f" {word} " not in f" {sql_upper} ", sql
        self._result = [r for r in self.rows if r[1].upper() in (params or [])]

    def fetchall(self):
        return self._result

    def close(self):
        pass


class FakeConn:
    def __init__(self, rows):
        self.rows, self.executed = rows, []

    def cursor(self):
        return FakeCursor(self.rows, self.executed)

    def close(self):
        pass


@pytest.fixture
def project(tmp_path, manifest_dict, monkeypatch):
    monkeypatch.delenv("DBT_PROFILES_DIR", raising=False)
    (tmp_path / "dbt_project.yml").write_text("name: jaffle\nprofile: jaffle\n")
    (tmp_path / "profiles.yml").write_text(
        "jaffle:\n  target: prod\n  outputs:\n    prod:\n      type: snowflake\n"
        "      account: acct\n      user: u\n      password: p\n      database: analytics\n      schema: prod\n"
    )
    (tmp_path / "target").mkdir()
    (tmp_path / "target" / "manifest.json").write_text(json.dumps(manifest_dict))
    return tmp_path


def _install_fake(monkeypatch, rows):
    conn = FakeConn(rows)
    monkeypatch.setattr(snowflake, "connect", lambda cfg: conn)
    return conn


ALL_PRESENT = [
    ("ANALYTICS", "STAGING", "STG_ORDERS", "VIEW", "NO"),
    ("ANALYTICS", "MARTS", "FCT_ORDERS", "BASE TABLE", "NO"),
    ("ANALYTICS", "MARTS", "CUSTOMERS", "BASE TABLE", "NO"),
    ("ANALYTICS", "SEEDS", "COUNTRY_CODES", "BASE TABLE", "NO"),
    ("ANALYTICS", "SNAPSHOTS", "ORDERS_SNAP", "BASE TABLE", "NO"),
]


def test_no_drift_exit_0(project, monkeypatch):
    _install_fake(monkeypatch, ALL_PRESENT)
    out = project / "drop.sql"
    code = cli.run(["--project-dir", str(project), "-o", str(out), "--max-manifest-age-hours", "0"])
    assert code == 0
    assert "Nothing to drop" in out.read_text()


def test_drift_exit_1_and_sql(project, monkeypatch, capsys):
    rows = ALL_PRESENT + [
        ("ANALYTICS", "MARTS", "LEGACY_REPORT", "VIEW", "NO"),
        ("ANALYTICS", "LEGACY", "OLD_STUFF", "BASE TABLE", "NO"),
    ]
    conn = _install_fake(monkeypatch, rows)
    out = project / "drop.sql"
    code = cli.run(
        [
            "--project-dir", str(project),
            "-o", str(out),
            "--include-schema", "legacy",
            "--format", "json",
            "--max-manifest-age-hours", "0",
        ]
    )
    assert code == 1
    sql = out.read_text()
    assert 'DROP VIEW IF EXISTS "ANALYTICS"."MARTS"."LEGACY_REPORT";' in sql
    assert 'DROP TABLE IF EXISTS "ANALYTICS"."LEGACY"."OLD_STUFF";' in sql
    data = json.loads(capsys.readouterr().out)
    assert {o["name"] for o in data["orphaned"]} == {"LEGACY_REPORT", "OLD_STUFF"}
    # one INFORMATION_SCHEMA query for the one database
    assert len(conn.executed) == 1
    assert '"ANALYTICS".INFORMATION_SCHEMA.TABLES' in conn.executed[0][0]


def test_dynamic_table_detected(project, monkeypatch):
    _install_fake(monkeypatch, ALL_PRESENT + [("ANALYTICS", "MARTS", "DT", "BASE TABLE", "YES")])
    out = project / "drop.sql"
    cli.run(["--project-dir", str(project), "-o", str(out), "--max-manifest-age-hours", "0"])
    assert 'DROP DYNAMIC TABLE IF EXISTS "ANALYTICS"."MARTS"."DT";' in out.read_text()


def test_stale_manifest_warns(project, manifest_dict, monkeypatch, capsys):
    manifest_dict["metadata"]["generated_at"] = "2020-01-01T00:00:00Z"
    (project / "target" / "manifest.json").write_text(json.dumps(manifest_dict))
    _install_fake(monkeypatch, ALL_PRESENT)
    cli.run(["--project-dir", str(project), "-o", str(project / "d.sql")])
    assert "manifest was generated" in capsys.readouterr().err


def test_wrong_target_warns(project, monkeypatch, capsys):
    rows = [("ANALYTICS", "MARTS", f"X{i}", "BASE TABLE", "NO") for i in range(8)]
    _install_fake(monkeypatch, rows)
    cli.run(["--project-dir", str(project), "-o", str(project / "d.sql"), "--max-manifest-age-hours", "0"])
    assert "different target" in capsys.readouterr().err


def test_missing_manifest_exit_2(tmp_path, monkeypatch):
    monkeypatch.delenv("DBT_PROFILES_DIR", raising=False)
    assert cli.run(["--project-dir", str(tmp_path)]) == 2
