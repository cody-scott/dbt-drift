"""Read-only Snowflake access: connect using a dbt profile target and list objects.

This module only ever issues SELECTs against INFORMATION_SCHEMA.
"""

from __future__ import annotations

import base64
from collections import defaultdict
from collections.abc import Iterable
from typing import Any

from .models import ObjectKind, Relation

QUERY_TAG = "dbt-drift (read-only scan)"


def _load_private_key(cfg: dict[str, Any]) -> bytes | None:
    """Return the private key as unencrypted PKCS8 DER, which the connector accepts."""
    raw: bytes | None = None
    if cfg.get("private_key_path"):
        with open(cfg["private_key_path"], "rb") as fh:
            raw = fh.read()
    elif cfg.get("private_key"):
        raw = str(cfg["private_key"]).encode()
    if raw is None:
        return None

    from cryptography.hazmat.primitives import serialization

    passphrase = cfg.get("private_key_passphrase")
    pw = passphrase.encode() if passphrase else None
    if raw.lstrip().startswith(b"-----BEGIN"):
        key = serialization.load_pem_private_key(raw, password=pw)
    else:  # dbt-snowflake also accepts base64-encoded DER
        key = serialization.load_der_private_key(base64.b64decode(raw), password=pw)
    return key.private_bytes(
        encoding=serialization.Encoding.DER,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    )


def connection_kwargs(cfg: dict[str, Any]) -> dict[str, Any]:
    """Map dbt-snowflake profile fields onto snowflake.connector.connect() kwargs."""
    kw: dict[str, Any] = {
        "account": cfg["account"],
        "user": cfg.get("user"),
        "role": cfg.get("role"),
        "warehouse": cfg.get("warehouse"),
        "database": cfg.get("database"),
        "session_parameters": {"QUERY_TAG": QUERY_TAG},
        "application": "dbt-drift",
    }
    if cfg.get("password"):
        kw["password"] = cfg["password"]
    if cfg.get("authenticator"):
        kw["authenticator"] = cfg["authenticator"]
    if cfg.get("token"):  # oauth
        kw["token"] = cfg["token"]
    if cfg.get("host"):
        kw["host"] = cfg["host"]
    if cfg.get("port"):
        kw["port"] = cfg["port"]
    if cfg.get("connect_timeout"):
        kw["login_timeout"] = cfg["connect_timeout"]
    if cfg.get("client_session_keep_alive") is not None:
        kw["client_session_keep_alive"] = cfg["client_session_keep_alive"]

    pk = _load_private_key(cfg)
    if pk:
        kw["private_key"] = pk
        kw.pop("password", None)
    return {k: v for k, v in kw.items() if v is not None}


def connect(cfg: dict[str, Any]):
    import snowflake.connector

    return snowflake.connector.connect(**connection_kwargs(cfg))


def quote_ident(name: str) -> str:
    return '"' + name.replace('"', '""') + '"'


def _kind(table_type: str, is_dynamic: str | None) -> ObjectKind:
    tt = (table_type or "").upper()
    if (is_dynamic or "").upper() == "YES":
        return ObjectKind.DYNAMIC_TABLE
    return {
        "BASE TABLE": ObjectKind.TABLE,
        "VIEW": ObjectKind.VIEW,
        "MATERIALIZED VIEW": ObjectKind.MATERIALIZED_VIEW,
        "EXTERNAL TABLE": ObjectKind.EXTERNAL_TABLE,
    }.get(tt, ObjectKind.UNKNOWN)


def list_relations(conn, schemas: Iterable[tuple[str, str]]) -> list[Relation]:
    """List tables/views in the given (database, schema) pairs.

    INFORMATION_SCHEMA is per database, so we issue one query per database.
    Schema names are passed as bind parameters; the database name can't be, so
    it is quoted as an identifier.
    """
    by_db: dict[str, set[str]] = defaultdict(set)
    for db, schema in schemas:
        by_db[db.upper()].add(schema.upper())

    results: list[Relation] = []
    cur = conn.cursor()
    try:
        for db, schema_set in sorted(by_db.items()):
            placeholders = ", ".join(["%s"] * len(schema_set))
            info_schema = f"{quote_ident(db)}.INFORMATION_SCHEMA.TABLES"
            base = (
                "SELECT TABLE_CATALOG, TABLE_SCHEMA, TABLE_NAME, TABLE_TYPE{extra} "
                f"FROM {info_schema} "
                f"WHERE UPPER(TABLE_SCHEMA) IN ({placeholders}) "
                "AND TABLE_TYPE <> 'TEMPORARY TABLE'"
            )
            params = sorted(schema_set)
            try:
                cur.execute(base.format(extra=", IS_DYNAMIC"), params)
                has_dynamic = True
            except Exception:
                # Older accounts may not expose IS_DYNAMIC; fall back without it.
                cur.execute(base.format(extra=""), params)
                has_dynamic = False
            for row in cur.fetchall():
                catalog, schema, name, table_type = row[:4]
                is_dyn = row[4] if has_dynamic else None
                results.append(Relation(catalog, schema, name, _kind(table_type, is_dyn)))
    finally:
        cur.close()
    return results
