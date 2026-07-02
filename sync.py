import logging
import sqlite3
import json
import requests
import os

logger = logging.getLogger("sync")

DB_PATH = os.environ.get("MTD_DB", "/opt/mtd-agent/cache.db")
API_URL = os.environ.get("MTD_API_URL", "https://api.mijnthuisdata.nl")


class SyncWorker:
    def __init__(self, config):
        self.config = config
        self.api_key = config.get("api_key")
        self._init_db()

    def _init_db(self):
        con = sqlite3.connect(DB_PATH)
        con.execute("""
            CREATE TABLE IF NOT EXISTS readings (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                integration TEXT,
                timestamp TEXT,
                data TEXT,
                synced INTEGER DEFAULT 0
            )
        """)
        con.commit()
        con.close()

    def store(self, integration: str, timestamp: str, data: dict):
        con = sqlite3.connect(DB_PATH)
        con.execute(
            "INSERT INTO readings (integration, timestamp, data) VALUES (?, ?, ?)",
            (integration, timestamp, json.dumps(data))
        )
        con.commit()
        con.close()
        logger.debug(f"Opgeslagen: {integration} @ {timestamp}")
        self._flush()

    def _flush(self):
        if not self.api_key:
            logger.warning("Geen API key, sync overgeslagen")
            return

        con = sqlite3.connect(DB_PATH)
        rows = con.execute(
            "SELECT id, integration, timestamp, data FROM readings WHERE synced = 0 LIMIT 50"
        ).fetchall()

        if not rows:
            con.close()
            return

        payload = [
            {"integration": r[1], "timestamp": r[2], "data": json.loads(r[3])}
            for r in rows
        ]

        try:
            resp = requests.post(
                f"{API_URL}/v1/readings",
                json=payload,
                headers={"X-API-Key": self.api_key},
                timeout=10
            )
            if resp.status_code == 200:
                ids = [r[0] for r in rows]
                con.execute(f"UPDATE readings SET synced = 1 WHERE id IN ({','.join('?'*len(ids))})", ids)
                con.commit()
                logger.info(f"{len(ids)} readings gesynchroniseerd")
            else:
                logger.warning(f"Sync mislukt: HTTP {resp.status_code}")
        except requests.RequestException as e:
            logger.warning(f"Sync fout: {e}")
        finally:
            con.close()
