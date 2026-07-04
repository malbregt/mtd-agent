"""Gedeeld state-bestand: de worker schrijft er periodiek zijn live status naar
(integraties, pending readings, uptime); core (en de statuspagina die daar
onderdeel van is) leest het uit. Zo kan core de laatst bekende worker-status
tonen zonder dat de twee processen in-memory objecten hoeven te delen — en
blijft de statuspagina bruikbaar (met 'laatst bekende' data) zelfs als de
worker zelf net crasht of aan het herstarten is.
"""
import json
import logging
import os
import time

logger = logging.getLogger("state")

STATE_PATH = os.environ.get("MTD_STATE", "/opt/mtd-agent/state.json")


def write(data: dict) -> None:
    """Schrijf de live worker-status atomisch weg (tmp-bestand + rename), zodat
    een lezer nooit een half geschreven bestand ziet."""
    tmp_path = f"{STATE_PATH}.tmp"
    try:
        with open(tmp_path, "w") as f:
            json.dump({**data, "written_at": time.time()}, f)
        os.replace(tmp_path, STATE_PATH)
    except Exception as e:
        logger.error(f"State wegschrijven mislukt: {e}")


def read() -> dict:
    """Lees de laatst bekende worker-status. Geeft een leeg dict terug als de
    worker nog nooit geschreven heeft (bijv. vlak na een herstart)."""
    try:
        with open(STATE_PATH) as f:
            return json.load(f)
    except Exception:
        return {}
