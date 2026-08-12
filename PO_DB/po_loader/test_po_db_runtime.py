"""Unit tests for PO_DB bootstrap helpers (no live PostgreSQL required)."""

from po_db_runtime import _quote_ident, _sync_bundled_port_config, connection_host


def test_connection_host_prefers_ipv4_loopback():
    assert connection_host("localhost") == "127.0.0.1"
    assert connection_host("LOCALHOST") == "127.0.0.1"
    assert connection_host("") == "127.0.0.1"
    assert connection_host("db.example.com") == "db.example.com"


def test_quote_ident_escapes_double_quotes():
    assert _quote_ident("IDP_USER") == '"IDP_USER"'
    assert _quote_ident('weird"name') == '"weird""name"'


def test_sync_bundled_port_config_updates_config_ini(tmp_path, monkeypatch):
    po_dir = tmp_path / "po-db"
    po_dir.mkdir()
    app_root = tmp_path
    cfg = po_dir / "config.ini"
    cfg.write_text(
        "[database]\nhost = 127.0.0.1\nport = 5432\ndbname = IDP\nuser = IDP_USER\npassword =\n",
        encoding="utf-8",
    )
    (app_root / ".env").write_text("POSTGRES_PORT=5432\n", encoding="utf-8")

    monkeypatch.setattr("po_db_runtime.po_db_dir", lambda: po_dir)
    monkeypatch.setattr("po_db_runtime.app_root", lambda: app_root)
    monkeypatch.setattr("po_db_runtime.config_path", lambda: cfg)

    _sync_bundled_port_config(15432)

    assert "port = 15432" in cfg.read_text(encoding="utf-8")
    assert "POSTGRES_PORT=15432" in (app_root / ".env").read_text(encoding="utf-8")
    _sync_bundled_port_config(15432)  # idempotent
    assert cfg.read_text(encoding="utf-8").count("port = 15432") == 1
