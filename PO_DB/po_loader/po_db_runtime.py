"""Portable PO_DB runtime: bundled/external PostgreSQL setup and schema bootstrap."""

from __future__ import annotations

import configparser
import logging
import os
import re
import socket
import subprocess
import sys
import time
from pathlib import Path

log = logging.getLogger("po_db")

INPROCESS_LOADER = "__inprocess__"
DEFAULT_BUNDLED_PORT_START = 15432
DEFAULT_EXTERNAL_PORT_START = 5432


def is_frozen() -> bool:
    return bool(getattr(sys, "frozen", False))


def po_db_dir() -> Path:
    if is_frozen():
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent.parent


def app_root() -> Path:
    return po_db_dir().parent


def _read_env_file(env_path: Path) -> dict[str, str]:
    if not env_path.is_file():
        return {}
    out: dict[str, str] = {}
    for line in env_path.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            continue
        key, value = stripped.split("=", 1)
        out[key.strip()] = value.strip().strip('"').strip("'")
    return out


def read_app_env(key: str, default: str = "") -> str:
    env_path = app_root() / ".env"
    values = _read_env_file(env_path)
    if key in values and values[key] != "":
        return values[key]
    return os.environ.get(key, default)


def use_bundled_postgres() -> bool:
    raw = read_app_env("IDP_USE_BUNDLED_POSTGRES", "1").strip().lower()
    if raw in {"0", "false", "no"}:
        return False
    host = read_app_env("POSTGRES_HOST", "localhost").strip().lower()
    if host not in {"localhost", "127.0.0.1", ""}:
        return False
    return (po_db_dir() / "pgsql" / "bin" / "postgres.exe").is_file()


def pgsql_bin() -> Path:
    return po_db_dir() / "pgsql" / "bin"


def bundled_pgdata() -> Path:
    return po_db_dir() / "pgdata"


def bundled_pgsql_env() -> dict[str, str]:
    env = os.environ.copy()
    env["PATH"] = str(pgsql_bin()) + os.pathsep + env.get("PATH", "")
    env["PGDATA"] = str(bundled_pgdata())
    return env


def port_open(host: str, port: int, timeout: float = 1.0) -> bool:
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False


def find_available_port(host: str, start_port: int, max_tries: int = 30) -> int:
    for offset in range(max_tries):
        port = start_port + offset
        if not port_open(host, port):
            return port
    raise RuntimeError(f"No free TCP port found starting at {start_port} for {host}")


def config_path() -> Path:
    return po_db_dir() / "config.ini"


def ensure_config_exists() -> Path:
    cfg = config_path()
    example = po_db_dir() / "config.example.ini"
    if not cfg.is_file() and example.is_file():
        cfg.write_text(example.read_text(encoding="utf-8"), encoding="utf-8")
    return cfg


def read_database_config(cfg_path: Path | None = None) -> dict[str, str]:
    cfg_path = cfg_path or ensure_config_exists()
    parser = configparser.ConfigParser()
    if not parser.read(cfg_path, encoding="utf-8"):
        raise RuntimeError(f"Could not read config: {cfg_path}")
    if "database" not in parser:
        raise RuntimeError(f"Missing [database] section in {cfg_path}")
    section = parser["database"]
    return {
        "host": section.get("host", read_app_env("POSTGRES_HOST", "localhost")),
        "port": str(section.get("port", read_app_env("POSTGRES_PORT", "5432"))),
        "dbname": section.get("dbname", read_app_env("POSTGRES_DB", "PO_DB")),
        "user": section.get("user", read_app_env("POSTGRES_USER", "postgres")),
        "password": section.get("password", read_app_env("POSTGRES_PASSWORD", "")),
    }


def sync_config_from_app_env() -> None:
    """Keep po-db/config.ini aligned with root .env POSTGRES_* values."""
    cfg = ensure_config_exists()
    mapping = {
        "host": read_app_env("POSTGRES_HOST", "localhost"),
        "port": read_app_env("POSTGRES_PORT", "5432"),
        "dbname": read_app_env("POSTGRES_DB", "PO_DB"),
        "user": read_app_env("POSTGRES_USER", "postgres"),
        "password": read_app_env("POSTGRES_PASSWORD", ""),
    }
    _write_ini_database_section(cfg, mapping)


def _write_ini_database_section(cfg_path: Path, mapping: dict[str, str]) -> None:
    lines = cfg_path.read_text(encoding="utf-8").splitlines() if cfg_path.is_file() else []
    in_database = False
    seen: set[str] = set()
    out: list[str] = []

    for line in lines:
        stripped = line.strip()
        if stripped.startswith("[") and stripped.endswith("]"):
            in_database = stripped.lower() == "[database]"
            out.append(line)
            continue
        if in_database:
            matched = False
            for key, value in mapping.items():
                if stripped.lower().startswith(f"{key} ="):
                    out.append(f"{key} = {value}")
                    seen.add(key)
                    matched = True
                    break
            if not matched:
                out.append(line)
            continue
        out.append(line)

    if seen != set(mapping.keys()):
        if out and out[-1].strip():
            out.append("")
        if not any(line.strip().lower() == "[database]" for line in out):
            out.append("[database]")
        for key, value in mapping.items():
            if key not in seen:
                out.append(f"{key} = {value}")

    cfg_path.write_text("\n".join(out) + "\n", encoding="utf-8")


def _write_env_postgres_port(port: int) -> None:
    env_path = app_root() / ".env"
    if not env_path.is_file():
        return
    pattern = re.compile(r"^(\s*POSTGRES_PORT\s*=\s*).*$", re.MULTILINE)
    text = env_path.read_text(encoding="utf-8")
    if pattern.search(text):
        text = pattern.sub(lambda m: f"{m.group(1)}{port}", text)
    else:
        if text and not text.endswith("\n"):
            text += "\n"
        text += f"POSTGRES_PORT={port}\n"
    env_path.write_text(text, encoding="utf-8")


def _read_bundled_postgres_port() -> int | None:
    conf = bundled_pgdata() / "postgresql.conf"
    if not conf.is_file():
        return None
    for line in conf.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if stripped.startswith("port") and "=" in stripped:
            try:
                return int(stripped.split("=", 1)[1].strip())
            except ValueError:
                return None
    return None


def _set_bundled_postgres_port(port: int) -> None:
    pgdata = bundled_pgdata()
    conf = pgdata / "postgresql.conf"
    lines: list[str] = []
    replaced = False
    if conf.is_file():
        for line in conf.read_text(encoding="utf-8").splitlines():
            if line.strip().startswith("port") and "=" in line.strip():
                lines.append(f"port = {port}")
                replaced = True
            else:
                lines.append(line)
    if not replaced:
        lines.append(f"port = {port}")
    conf.write_text("\n".join(lines) + "\n", encoding="utf-8")

    mapping = read_database_config()
    mapping["port"] = str(port)
    _write_ini_database_section(config_path(), mapping)
    _write_env_postgres_port(port)


def resolve_bundled_port(requested_port: int) -> int:
    host = "127.0.0.1"
    existing = _read_bundled_postgres_port()
    if existing and port_open(host, existing):
        log.info("Bundled PostgreSQL already listening on port %s.", existing)
        mapping = read_database_config()
        mapping["port"] = str(existing)
        _write_ini_database_section(config_path(), mapping)
        _write_env_postgres_port(existing)
        return existing

    if not port_open(host, requested_port):
        return requested_port

    fallback = find_available_port(host, DEFAULT_BUNDLED_PORT_START)
    log.warning(
        "Port %s is already in use. Bundled PostgreSQL will use port %s instead.",
        requested_port,
        fallback,
    )
    _set_bundled_postgres_port(fallback)
    return fallback


def ensure_bundled_cluster_initialized(port: int) -> None:
    pgdata = bundled_pgdata()
    initdb = pgsql_bin() / "initdb.exe"
    if (pgdata / "PG_VERSION").is_file():
        _set_bundled_postgres_port(port)
        return
    if not initdb.is_file():
        raise RuntimeError(f"Bundled PostgreSQL not found at {pgsql_bin()}")

    log.info("Initializing bundled PostgreSQL data directory (first run)...")
    pgdata.mkdir(parents=True, exist_ok=True)
    (po_db_dir() / "logs").mkdir(parents=True, exist_ok=True)
    subprocess.run(
        [str(initdb), "-D", str(pgdata), "-U", "postgres", "-A", "trust", "-E", "UTF8"],
        cwd=str(pgsql_bin()),
        env=bundled_pgsql_env(),
        check=True,
    )
    conf = pgdata / "postgresql.conf"
    with conf.open("a", encoding="utf-8") as fh:
        fh.write(f"\nport = {port}\n")
        fh.write("listen_addresses = '127.0.0.1'\n")


def start_bundled_postgres(port: int) -> None:
    host = "127.0.0.1"
    if port_open(host, port):
        log.info("PostgreSQL already listening on %s:%s.", host, port)
        return

    pg_ctl = pgsql_bin() / "pg_ctl.exe"
    if not pg_ctl.is_file():
        raise RuntimeError(f"pg_ctl.exe not found at {pg_ctl}")

    ensure_bundled_cluster_initialized(port)
    logpath = po_db_dir() / "logs" / "postgres.log"
    log.info("Starting bundled PostgreSQL on port %s ...", port)
    subprocess.run(
        [str(pg_ctl), "start", "-D", str(bundled_pgdata()), "-l", str(logpath), "-w"],
        cwd=str(pgsql_bin()),
        env=bundled_pgsql_env(),
        check=True,
    )

    deadline = time.time() + 120
    while time.time() < deadline:
        if port_open(host, port):
            log.info("Bundled PostgreSQL is ready.")
            return
        time.sleep(1.0)
    raise RuntimeError(f"Bundled PostgreSQL did not become ready on port {port}. See {logpath}")


def _psycopg_connect_kwargs(db: dict[str, str], dbname: str | None = None) -> dict:
    return {
        "host": db["host"],
        "port": int(db["port"]),
        "dbname": dbname if dbname is not None else db["dbname"],
        "user": db["user"],
        "password": db.get("password") or "",
        "connect_timeout": 10,
    }


def ensure_po_db_schema(db: dict[str, str]) -> None:
    import psycopg2
    from psycopg2.extensions import ISOLATION_LEVEL_AUTOCOMMIT

    sql_file = po_db_dir() / "sql" / "create_po_database.sql"
    if not sql_file.is_file():
        raise RuntimeError(f"Schema SQL not found: {sql_file}")

    dbname = db["dbname"]
    admin_kwargs = _psycopg_connect_kwargs(db, dbname="postgres")
    log.info("Ensuring database %r exists on %s:%s ...", dbname, db["host"], db["port"])
    admin_conn = psycopg2.connect(**admin_kwargs)
    admin_conn.set_isolation_level(ISOLATION_LEVEL_AUTOCOMMIT)
    try:
        with admin_conn.cursor() as cur:
            cur.execute("SELECT 1 FROM pg_database WHERE datname = %s", (dbname,))
            if not cur.fetchone():
                log.info('Creating database "%s" ...', dbname)
                cur.execute(f'CREATE DATABASE "{dbname}"')
    finally:
        admin_conn.close()

    app_kwargs = _psycopg_connect_kwargs(db)
    conn = psycopg2.connect(**app_kwargs)
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT 1 FROM information_schema.tables WHERE table_name = %s",
                ("vendor_master",),
            )
            if cur.fetchone():
                log.info("PO_DB schema already present.")
                return
        conn.commit()
    finally:
        conn.close()

    log.info("Applying schema from %s ...", sql_file.name)
    schema_sql = sql_file.read_text(encoding="utf-8")
    conn = psycopg2.connect(**app_kwargs)
    try:
        with conn.cursor() as cur:
            cur.execute(schema_sql)
        conn.commit()
        log.info("PO_DB schema applied.")
    finally:
        conn.close()


def ensure_layout() -> None:
    base = po_db_dir()
    for name in ("data", "data/completed", "data/ERROR", "pgdata", "logs", "sql"):
        (base / name).mkdir(parents=True, exist_ok=True)


def bootstrap_po_db() -> dict[str, str]:
    """Start/configure PostgreSQL and ensure PO_DB + tables exist. Returns DB config."""
    ensure_layout()
    sync_config_from_app_env()
    db = read_database_config()

    if not read_app_env("POSTGRES_HOST", "localhost").strip():
        raise RuntimeError("POSTGRES_HOST is not set — PO_DB is disabled.")

    if use_bundled_postgres():
        requested = int(db.get("port") or DEFAULT_BUNDLED_PORT_START)
        if requested == DEFAULT_EXTERNAL_PORT_START and port_open("127.0.0.1", requested):
            requested = DEFAULT_BUNDLED_PORT_START
        port = resolve_bundled_port(requested)
        start_bundled_postgres(port)
        db = read_database_config()
    else:
        log.info(
            "Using external PostgreSQL at %s:%s (IDP_USE_BUNDLED_POSTGRES=0 or non-local host).",
            db["host"],
            db["port"],
        )
        import psycopg2

        try:
            psycopg2.connect(**_psycopg_connect_kwargs(db, dbname="postgres"))
        except psycopg2.OperationalError as exc:
            raise RuntimeError(f"Could not connect to external PostgreSQL: {exc}") from exc

    ensure_po_db_schema(db)
    return read_database_config()
