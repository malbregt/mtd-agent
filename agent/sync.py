import logging
import sqlite3
import json
import os

logger = logging.getLogger("sync")

DB_PATH = os.environ.get("MTD_DB", "/opt/mtd-agent/cache.db")


class SyncWorker:
    def __init__(self, api_client):
        self.api = api_client
        self._init_db()

    def _init_db(self):
        con = sqlite3.connect(DB_PATH)
        con.execute("""
            CREATE TABLE IF NOT EXISTS readings (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                integration_id TEXT,
                customer_integration_id TEXT,
                timestamp TEXT,
                data TEXT,
                synced INTEGER DEFAULT 0
            )
        """)
        try:
            con.execute("ALTER TABLE readings ADD COLUMN customer_integration_id TEXT")
        except sqlite3.OperationalError:
            pass  # kolom bestaat al
        con.commit()
        con.close()

    def store(self, integration_id: str, timestamp: str, data: dict, customer_integration_id: str = None):
        """Sla reading op in lokale cache."""
        con = sqlite3.connect(DB_PATH)
        con.execute(
            "INSERT INTO readings (integration_id, customer_integration_id, timestamp, data) VALUES (?, ?, ?, ?)",
            (integration_id, customer_integration_id, timestamp, json.dumps(data))
        )
        con.commit()
        con.close()
        logger.debug(f"Opgeslagen: {integration_id} ({customer_integration_id}) @ {timestamp}")

    def flush(self):
        """Stuur openstaande readings naar platform."""
        con = sqlite3.connect(DB_PATH)
        rows = con.execute(
            "SELECT id, integration_id, customer_integration_id, timestamp, data FROM readings WHERE synced = 0 LIMIT 100"
        ).fetchall()

        if not rows:
            con.close()
            return

        payload = [
            {
                "integration_id": r[1],
                "customer_integration_id": r[2],
                "timestamp": r[3],
                "data": json.loads(r[4])
            }
            for r in rows
        ]

        if self.api.send_readings(payload):
            ids = [r[0] for r in rows]
            con.execute(
                f"UPDATE readings SET synced = 1 WHERE id IN ({','.join('?'*len(ids))})",
                ids
            )
            con.commit()
            logger.info(f"{len(ids)} readings gesynchroniseerd")
        else:
            logger.warning("Sync mislukt, readings bewaard in cache")

        # Opruimen: verwijder gesynchroniseerde readings ouder dan 48 uur
        con.execute("""
            DELETE FROM readings
            WHERE synced = 1
            AND datetime(timestamp) < datetime('now', '-48 hours')
        """)
        con.commit()
        con.close()
