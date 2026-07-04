"""Lokale signaal-queue tussen mtd-core en mtd-worker (twee losse processen).

Core ontvangt WebSocket-berichten die de worker moeten raken (config_update,
restart_integration) maar heeft zelf geen toegang tot de integratie-objecten
(die leven in het worker-proces). Core zet zo'n bericht om in een signaal in
dit bestand; worker leest en verwerkt het op zijn eigen tempo, in zijn eigen
loop, zodat een falende/hangende integratie de core nooit blokkeert en een
falende/hangende core de worker nooit blokkeert.

Bestandslock (fcntl) voorkomt races tussen de twee processen bij gelijktijdig
lezen/schrijven op hetzelfde bestand.
"""
import fcntl
import json
import logging
import os
import time

logger = logging.getLogger("signals")

SIGNALS_PATH = os.environ.get("MTD_SIGNALS", "/opt/mtd-agent/signals.json")


def _load_locked(f) -> list[dict]:
    f.seek(0)
    content = f.read()
    return json.loads(content) if content else []


def _save_locked(f, signals: list[dict]) -> None:
    f.seek(0)
    f.truncate()
    json.dump(signals, f)


def push(signal_type: str, payload: dict | None = None) -> None:
    """Voeg een signaal toe voor de worker (bijv. 'config_update', 'restart_integration')."""
    entry = {"type": signal_type, "payload": payload or {}, "ts": time.time()}
    try:
        if not os.path.exists(SIGNALS_PATH):
            open(SIGNALS_PATH, "w").close()
        with open(SIGNALS_PATH, "r+") as f:
            fcntl.flock(f, fcntl.LOCK_EX)
            try:
                pending = _load_locked(f)
                pending.append(entry)
                _save_locked(f, pending)
            finally:
                fcntl.flock(f, fcntl.LOCK_UN)
    except Exception as e:
        logger.error(f"Signaal wegschrijven mislukt ({signal_type}): {e}")


def pop_all() -> list[dict]:
    """Haal alle wachtende signalen op en leeg de queue. Door de worker elke
    loop-iteratie aangeroepen; geeft een lege lijst als er niets klaarstaat."""
    if not os.path.exists(SIGNALS_PATH):
        return []
    try:
        with open(SIGNALS_PATH, "r+") as f:
            fcntl.flock(f, fcntl.LOCK_EX)
            try:
                pending = _load_locked(f)
                _save_locked(f, [])
            finally:
                fcntl.flock(f, fcntl.LOCK_UN)
        return pending
    except Exception as e:
        logger.error(f"Signalen lezen mislukt: {e}")
        return []
