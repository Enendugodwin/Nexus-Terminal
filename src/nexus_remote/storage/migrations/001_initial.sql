-- 001_initial.sql - core schema for Nexus Remote.
-- Secrets are never stored here: credential_refs only holds vault key names.

CREATE TABLE IF NOT EXISTS device_groups (
    id          TEXT PRIMARY KEY,
    name        TEXT NOT NULL UNIQUE,
    description TEXT NOT NULL DEFAULT '',
    created_at  TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS devices (
    id                TEXT PRIMARY KEY,
    name              TEXT NOT NULL,
    hostname          TEXT NOT NULL,
    operating_system  TEXT NOT NULL DEFAULT 'unknown',
    environment       TEXT NOT NULL DEFAULT 'unspecified',
    criticality       TEXT NOT NULL DEFAULT 'standard',
    tags              TEXT NOT NULL DEFAULT '[]',
    description       TEXT NOT NULL DEFAULT '',
    owner             TEXT NOT NULL DEFAULT '',
    notes             TEXT NOT NULL DEFAULT '',
    favorite          INTEGER NOT NULL DEFAULT 0,
    group_id          TEXT REFERENCES device_groups(id) ON DELETE SET NULL,
    last_connected_at TEXT,
    last_connection_ok INTEGER,
    created_at        TEXT NOT NULL,
    updated_at        TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_devices_hostname ON devices(hostname);
CREATE INDEX IF NOT EXISTS idx_devices_group ON devices(group_id);

CREATE TABLE IF NOT EXISTS connection_profiles (
    id          TEXT PRIMARY KEY,
    device_id   TEXT NOT NULL REFERENCES devices(id) ON DELETE CASCADE,
    protocol    TEXT NOT NULL,
    enabled     INTEGER NOT NULL DEFAULT 1,
    port        INTEGER,
    auth_method TEXT NOT NULL DEFAULT 'credential_vault',
    username    TEXT NOT NULL DEFAULT '',
    extra       TEXT NOT NULL DEFAULT '{}',
    UNIQUE (device_id, protocol)
);

CREATE TABLE IF NOT EXISTS routing_profiles (
    device_id        TEXT PRIMARY KEY REFERENCES devices(id) ON DELETE CASCADE,
    preferred_method TEXT NOT NULL DEFAULT 'direct',
    fallbacks        TEXT NOT NULL DEFAULT '[]',
    jump_host        TEXT,
    gateway          TEXT,
    relay            TEXT,
    agent_id         TEXT
);

-- Reference only: the vault key name, never the secret itself.
CREATE TABLE IF NOT EXISTS credential_refs (
    id         TEXT PRIMARY KEY,
    device_id  TEXT REFERENCES devices(id) ON DELETE CASCADE,
    kind       TEXT NOT NULL,
    username   TEXT NOT NULL DEFAULT '',
    key_name   TEXT NOT NULL UNIQUE,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS host_keys (
    host        TEXT NOT NULL,
    key_type    TEXT NOT NULL,
    fingerprint TEXT NOT NULL,
    key_blob    TEXT,
    status      TEXT NOT NULL DEFAULT 'trusted',
    source      TEXT NOT NULL DEFAULT 'user',
    trusted_at  TEXT NOT NULL,
    PRIMARY KEY (host, key_type)
);

CREATE TABLE IF NOT EXISTS command_history (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    ts          TEXT NOT NULL,
    device_id   TEXT,
    environment TEXT NOT NULL DEFAULT 'unknown',
    command     TEXT NOT NULL,
    status      TEXT NOT NULL DEFAULT 'suggested',
    source      TEXT NOT NULL DEFAULT 'history',
    exit_code   INTEGER,
    risk        TEXT NOT NULL DEFAULT 'unknown',
    notes       TEXT NOT NULL DEFAULT ''
);

CREATE INDEX IF NOT EXISTS idx_history_ts ON command_history(ts DESC);

CREATE TABLE IF NOT EXISTS command_snippets (
    id          TEXT PRIMARY KEY,
    name        TEXT NOT NULL,
    command     TEXT NOT NULL,
    environment TEXT NOT NULL DEFAULT 'unknown',
    description TEXT NOT NULL DEFAULT '',
    verified    INTEGER NOT NULL DEFAULT 0,
    risk        TEXT NOT NULL DEFAULT 'unknown',
    tags        TEXT NOT NULL DEFAULT '[]',
    created_at  TEXT NOT NULL,
    updated_at  TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS known_errors (
    id            TEXT PRIMARY KEY,
    pattern       TEXT NOT NULL,
    environment   TEXT NOT NULL DEFAULT 'any',
    title         TEXT NOT NULL,
    causes        TEXT NOT NULL DEFAULT '[]',
    next_steps    TEXT NOT NULL DEFAULT '[]',
    source        TEXT NOT NULL DEFAULT 'built-in rules',
    confirmations INTEGER NOT NULL DEFAULT 0,
    created_at    TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS documents (
    id         TEXT PRIMARY KEY,
    title      TEXT NOT NULL,
    environment TEXT NOT NULL DEFAULT 'any',
    body       TEXT NOT NULL,
    source     TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS audit_events (
    id        INTEGER PRIMARY KEY AUTOINCREMENT,
    ts        TEXT NOT NULL,
    category  TEXT NOT NULL,
    action    TEXT NOT NULL,
    severity  TEXT NOT NULL DEFAULT 'info',
    outcome   TEXT NOT NULL DEFAULT 'info',
    actor     TEXT NOT NULL DEFAULT 'local-user',
    device_id TEXT,
    subject   TEXT NOT NULL DEFAULT '',
    detail    TEXT NOT NULL DEFAULT '{}'
);

CREATE INDEX IF NOT EXISTS idx_audit_ts ON audit_events(ts DESC);

CREATE TABLE IF NOT EXISTS settings (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
