import sqlite3
from contextlib import contextmanager

import config

SCHEMA = """
CREATE TABLE IF NOT EXISTS device_config (
    key   TEXT PRIMARY KEY,
    value TEXT
);

CREATE TABLE IF NOT EXISTS agent_plugins (
    plugin_id         TEXT PRIMARY KEY,
    target_version    TEXT,
    installed_version TEXT,
    config            TEXT,
    status            TEXT,
    updated_at        TEXT
);

CREATE TABLE IF NOT EXISTS readings (
    id        INTEGER PRIMARY KEY AUTOINCREMENT,
    device_id TEXT,
    metric    TEXT,
    value     REAL,
    unit      TEXT,
    direction TEXT,
    source    TEXT,
    timestamp TEXT,
    synced    INTEGER DEFAULT 0
);

CREATE TABLE IF NOT EXISTS plugin_health (
    plugin_id        TEXT PRIMARY KEY,
    status           TEXT,
    last_reading_at  TEXT,
    last_error       TEXT,
    restart_count    INTEGER DEFAULT 0,
    updated_at       TEXT
);

CREATE TABLE IF NOT EXISTS commands (
    id          TEXT PRIMARY KEY,
    plugin_id   TEXT,
    action      TEXT,
    params      TEXT,
    status      TEXT,
    created_at  TEXT,
    executed_at TEXT
);
"""


def init_db(db_path: str | None = None) -> None:
    with _connect(db_path) as conn:
        conn.executescript(SCHEMA)
        conn.commit()


@contextmanager
def _connect(db_path: str | None = None):
    conn = sqlite3.connect(db_path or config.DB_PATH)
    conn.row_factory = sqlite3.Row
    try:
        yield conn
    finally:
        conn.close()


def get_device_config(key: str, default=None):
    with _connect() as conn:
        row = conn.execute("SELECT value FROM device_config WHERE key = ?", (key,)).fetchone()
        return row["value"] if row else default


def set_device_config(key: str, value: str) -> None:
    with _connect() as conn:
        conn.execute(
            "INSERT INTO device_config (key, value) VALUES (?, ?) "
            "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
            (key, value),
        )
        conn.commit()


def load_installed_plugins() -> list[sqlite3.Row]:
    """Plugins die eerder succesvol geïnstalleerd zijn — geladen bij opstart
    zodat de agent zonder platformverbinding kan doordraaien."""
    with _connect() as conn:
        return conn.execute(
            "SELECT * FROM agent_plugins WHERE status = 'installed'"
        ).fetchall()


def upsert_plugin(plugin_id: str, target_version: str | None = None,
                   installed_version: str | None = None, config_json: str | None = None,
                   status: str | None = None) -> None:
    with _connect() as conn:
        existing = conn.execute(
            "SELECT * FROM agent_plugins WHERE plugin_id = ?", (plugin_id,)
        ).fetchone()
        if existing:
            conn.execute(
                """UPDATE agent_plugins SET
                    target_version = COALESCE(?, target_version),
                    installed_version = COALESCE(?, installed_version),
                    config = COALESCE(?, config),
                    status = COALESCE(?, status),
                    updated_at = datetime('now')
                   WHERE plugin_id = ?""",
                (target_version, installed_version, config_json, status, plugin_id),
            )
        else:
            conn.execute(
                """INSERT INTO agent_plugins
                   (plugin_id, target_version, installed_version, config, status, updated_at)
                   VALUES (?, ?, ?, ?, ?, datetime('now'))""",
                (plugin_id, target_version, installed_version, config_json, status or "pending"),
            )
        conn.commit()


def store_reading(device_id: str, metric: str, value: float, unit: str,
                   direction: str, source: str, timestamp: str) -> None:
    with _connect() as conn:
        conn.execute(
            """INSERT INTO readings (device_id, metric, value, unit, direction, source, timestamp)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (device_id, metric, value, unit, direction, source, timestamp),
        )
        conn.commit()


def unsynced_readings(limit: int = 500) -> list[sqlite3.Row]:
    with _connect() as conn:
        return conn.execute(
            "SELECT * FROM readings WHERE synced = 0 ORDER BY id LIMIT ?", (limit,)
        ).fetchall()


def mark_synced(ids: list[int]) -> None:
    if not ids:
        return
    with _connect() as conn:
        placeholders = ",".join("?" for _ in ids)
        conn.execute(f"UPDATE readings SET synced = 1 WHERE id IN ({placeholders})", ids)
        conn.commit()


def upsert_plugin_health(plugin_id: str, status: str, last_reading_at: str | None,
                          last_error: str | None, restart_count: int) -> None:
    with _connect() as conn:
        conn.execute(
            """INSERT INTO plugin_health (plugin_id, status, last_reading_at, last_error, restart_count, updated_at)
               VALUES (?, ?, ?, ?, ?, datetime('now'))
               ON CONFLICT(plugin_id) DO UPDATE SET
                   status = excluded.status,
                   last_reading_at = excluded.last_reading_at,
                   last_error = excluded.last_error,
                   restart_count = excluded.restart_count,
                   updated_at = excluded.updated_at""",
            (plugin_id, status, last_reading_at, last_error, restart_count),
        )
        conn.commit()


def all_plugin_health() -> list[sqlite3.Row]:
    with _connect() as conn:
        return conn.execute("SELECT * FROM plugin_health").fetchall()


def log_command(command_id: str, plugin_id: str, action: str, params_json: str, status: str) -> None:
    with _connect() as conn:
        conn.execute(
            """INSERT INTO commands (id, plugin_id, action, params, status, created_at)
               VALUES (?, ?, ?, ?, ?, datetime('now'))
               ON CONFLICT(id) DO UPDATE SET status = excluded.status""",
            (command_id, plugin_id, action, params_json, status),
        )
        conn.commit()
