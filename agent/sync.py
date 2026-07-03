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

        payload = []
        bad_ids = []
        for r in rows:
            data = json.loads(r[4])
            if not isinstance(data, dict):
                # Kapotte/oude cache-entry (bv. een foutobject i.p.v. echte data die per
                # ongeluk werd opgeslagen). Nooit blijven herhalen, anders vergiftigt 1
                # kapotte rij permanent de hele batch voor alle integraties.
                logger.error(f"Ongeldige reading in cache overgeslagen (id={r[0]}, integratie={r[1]}): {data}")
                bad_ids.append(r[0])
                continue
            payload.append({
                "integration_id": r[1],
                "customer_integration_id": r[2],
                "timestamp": r[3],
                "data": data
            })

        if bad_ids:
            con.execute(
                f"DELETE FROM readings WHERE id IN ({','.join('?'*len(bad_ids))})",
                bad_ids
            )
            con.commit()

        if payload and self.api.send_readings(payload):
            ids = [r[0] for r in rows if r[0] not in bad_ids]
            con.execute(
                f"UPDATE readings SET synced = 1 WHERE id IN ({','.join('?'*len(ids))})",
                ids
            )
            con.commit()
            logger.info(f"{len(ids)} readings gesynchroniseerd")
        elif payload:
            logger.warning("Sync mislukt, readings bewaard in cache")

        # Opruimen: verwijder gesynchroniseerde readings ouder dan 48 uur
        con.execute("""
            DELETE FROM readings
            WHERE synced = 1
            AND datetime(timestamp) < datetime('now', '-48 hours')
        """)
        con.commit()
        con.close()
