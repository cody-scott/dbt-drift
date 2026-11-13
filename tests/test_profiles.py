import pytest

from dbt_drift.profiles import ProfileError, resolve_target

PROFILES = """
jaffle:
  target: dev
  outputs:
    dev:
      type: snowflake
      account: "{{ env_var('SF_ACCOUNT') }}"
      user: dev_user
      password: "{{ env_var('SF_PASSWORD', '123456') }}"
      database: analytics_dev
      schema: dbt_dev
      client_session_keep_alive: "{{ env_var('SF_KEEPALIVE', 'false') | as_bool }}"
    prod:
      type: snowflake
      account: acct
      user: prod_user
      database: analytics
      schema: prod
    pg:
      type: postgres
"""


@pytest.fixture
def project(tmp_path):
    (tmp_path / "dbt_project.yml").write_text("name: jaffle\nprofile: jaffle\n")
    (tmp_path / "profiles.yml").write_text(PROFILES)
    return tmp_path


def test_default_target_with_env(project, monkeypatch):
    monkeypatch.delenv("DBT_PROFILES_DIR", raising=False)
    monkeypatch.setenv("SF_ACCOUNT", "xy123")
    t = resolve_target(project)
    assert t.target_name == "dev"
    assert t.config["account"] == "xy123"
    assert t.config["password"] == "123456"  # numeric-looking stays a string
    assert t.config["client_session_keep_alive"] is False


def test_explicit_target(project, monkeypatch):
    monkeypatch.delenv("DBT_PROFILES_DIR", raising=False)
    assert resolve_target(project, target="prod").config["database"] == "analytics"


def test_missing_env_var(project, monkeypatch):
    monkeypatch.delenv("DBT_PROFILES_DIR", raising=False)
    monkeypatch.delenv("SF_ACCOUNT", raising=False)
    with pytest.raises(ProfileError, match="SF_ACCOUNT"):
        resolve_target(project)


def test_non_snowflake_rejected(project, monkeypatch):
    monkeypatch.delenv("DBT_PROFILES_DIR", raising=False)
    with pytest.raises(ProfileError, match="only 'snowflake'"):
        resolve_target(project, target="pg")


def test_unknown_target(project, monkeypatch):
    monkeypatch.delenv("DBT_PROFILES_DIR", raising=False)
    with pytest.raises(ProfileError, match="available: dev, prod, pg"):
        resolve_target(project, target="qa")


def test_profiles_dir_env(project, tmp_path_factory, monkeypatch):
    other = tmp_path_factory.mktemp("elsewhere")
    (other / "profiles.yml").write_text(PROFILES.replace("analytics\n", "analytics_from_env_dir\n"))
    monkeypatch.setenv("DBT_PROFILES_DIR", str(other))
    assert resolve_target(project, target="prod").config["database"] == "analytics_from_env_dir"
