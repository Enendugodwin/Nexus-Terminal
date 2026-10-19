"""Database schema, migrations and settings."""

from __future__ import annotations

from nexus_remote.storage.database import Database, supports_fts5


def test_initialize_creates_schema(database):
    tables = {
        row["name"]
        for row in database.query("SELECT name FROM sqlite_master WHERE type = 'table'")
    }
    for expected in (
        "devices",
        "connection_profiles",
        "routing_profiles",
        "credential_refs",
        "host_keys",
        "command_history",
        "command_snippets",
        "known_errors",
        "audit_events",
        "settings",
    ):
        assert expected in tables


def test_migrations_are_idempotent(tmp_path):
    path = tmp_path / "db.sqlite"
    first = Database(path).initialize()
    version = first.query_one("PRAGMA user_version")[0]
    first.close()

    second = Database(path).initialize()
    assert second.query_one("PRAGMA user_version")[0] == version
    assert second.migrate() == []
    second.close()


def test_fts5_probe_matches_capability(database):
    assert supports_fts5(database.connection) is database.fts_enabled


def test_settings_roundtrip(database):
    assert database.get_setting("missing", "fallback") == "fallback"
    database.set_setting("policy.allow_destructive", "1")
    assert database.get_setting("policy.allow_destructive") == "1"
    database.set_setting("policy.allow_destructive", "0")
    assert database.get_setting("policy.allow_destructive") == "0"


def test_transaction_rolls_back_on_error(database):
    database.set_setting("keep", "yes")
    try:
        with database.transaction() as connection:
            connection.execute("INSERT INTO settings (key, value) VALUES ('temp', 'x')")
            raise RuntimeError("boom")
    except RuntimeError:
        pass
    assert database.get_setting("temp") is None
    assert database.get_setting("keep") == "yes"


def test_command_history_fts_roundtrip(database):
    if not database.fts_enabled:
        return
    database.execute(
        "INSERT INTO command_history (ts, environment, command, status) VALUES ('2026-01-01T00:00:00+00:00', 'bash', 'systemctl restart nginx', 'executed')"
    )
    rows = database.query(
        "SELECT command FROM command_history_fts WHERE command_history_fts MATCH 'nginx'"
    )
    assert rows and rows[0]["command"] == "systemctl restart nginx"


def test_foreign_keys_cascade(tmp_path):
    db = Database(tmp_path / "fk.db").initialize()
    db.execute("INSERT INTO devices (id, name, hostname, created_at, updated_at) VALUES ('d1','d','1.2.3.4','t','t')")
    db.execute(
        "INSERT INTO connection_profiles (id, device_id, protocol, port) VALUES ('c1','d1','ssh',22)"
    )
    db.execute("DELETE FROM devices WHERE id = 'd1'")
    assert db.query("SELECT * FROM connection_profiles") == []
    db.close()
