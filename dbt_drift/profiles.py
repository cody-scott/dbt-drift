"""Resolve Snowflake connection settings from dbt's profiles.yml.

Mirrors dbt's lookup rules closely enough to reuse the same credentials:
  profiles dir: --profiles-dir > $DBT_PROFILES_DIR > <project-dir>/profiles.yml > ~/.dbt
  profile name: --profile > `profile:` in dbt_project.yml
  target:       --target > `target:` in the profile
Supports the `env_var()` Jinja function plus the as_bool/as_number/as_text filters.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml
from jinja2 import Environment, StrictUndefined


class ProfileError(Exception):
    pass


_MISSING = object()


def _env_var(name: str, default: Any = _MISSING) -> str:
    if name in os.environ:
        return os.environ[name]
    if default is not _MISSING:
        return default
    raise ProfileError(f"env var '{name}' referenced in profiles.yml is not set")


def _as_bool(v: Any) -> bool:
    return str(v).strip().lower() in {"1", "true", "yes", "y", "on"}


def _as_number(v: Any) -> float | int:
    s = str(v).strip()
    return int(s) if s.lstrip("-").isdigit() else float(s)


_jinja = Environment(undefined=StrictUndefined)
_jinja.globals["env_var"] = _env_var
_jinja.filters["as_bool"] = _as_bool
_jinja.filters["as_number"] = _as_number
_jinja.filters["as_text"] = str


def _render(value: Any) -> Any:
    if isinstance(value, dict):
        return {k: _render(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_render(v) for v in value]
    if isinstance(value, str) and ("{{" in value or "{%" in value):
        stripped = value.strip()
        # Like dbt: a lone `{{ ... | as_bool }}` / `as_number` expression keeps its native
        # type; everything else renders to a string (so numeric-looking passwords stay strings).
        if stripped.startswith("{{") and stripped.endswith("}}") and stripped.count("{{") == 1:
            expr = stripped[2:-2].strip()
            if any(f in expr.replace(" ", "") for f in ("|as_bool", "|as_number")):
                return _jinja.compile_expression(expr)()
        return _jinja.from_string(value).render()
    return value


@dataclass
class ResolvedTarget:
    profile_name: str
    target_name: str
    profiles_path: Path
    config: dict[str, Any]


def find_profiles_dir(project_dir: Path, profiles_dir: str | None) -> Path:
    if profiles_dir:
        return Path(profiles_dir).expanduser()
    if os.environ.get("DBT_PROFILES_DIR"):
        return Path(os.environ["DBT_PROFILES_DIR"]).expanduser()
    if (project_dir / "profiles.yml").exists():
        return project_dir
    return Path("~/.dbt").expanduser()


def project_profile_name(project_dir: Path) -> str:
    proj_file = project_dir / "dbt_project.yml"
    if not proj_file.exists():
        raise ProfileError(f"no dbt_project.yml in {project_dir}; pass --profile explicitly")
    proj = yaml.safe_load(proj_file.read_text()) or {}
    name = proj.get("profile")
    if not name:
        raise ProfileError(f"{proj_file} has no 'profile:' key; pass --profile explicitly")
    return _render(name)


def resolve_target(
    project_dir: str | Path = ".",
    profiles_dir: str | None = None,
    profile: str | None = None,
    target: str | None = None,
) -> ResolvedTarget:
    project_dir = Path(project_dir).expanduser()
    pdir = find_profiles_dir(project_dir, profiles_dir)
    ppath = pdir / "profiles.yml"
    if not ppath.exists():
        raise ProfileError(f"profiles.yml not found at {ppath}")

    profiles = yaml.safe_load(ppath.read_text()) or {}
    profile_name = profile or project_profile_name(project_dir)
    if profile_name not in profiles:
        available = ", ".join(k for k in profiles if k != "config") or "none"
        raise ProfileError(f"profile '{profile_name}' not in {ppath} (available: {available})")

    prof = profiles[profile_name]
    target_name = target or _render(prof.get("target"))
    outputs = prof.get("outputs") or {}
    if not target_name or target_name not in outputs:
        raise ProfileError(
            f"target '{target_name}' not found in profile '{profile_name}' "
            f"(available: {', '.join(outputs) or 'none'})"
        )

    cfg = _render(outputs[target_name])
    if cfg.get("type") != "snowflake":
        raise ProfileError(f"target '{target_name}' is type '{cfg.get('type')}', only 'snowflake' is supported")
    return ResolvedTarget(profile_name, target_name, ppath, cfg)
