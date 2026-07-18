"""MTD Worker — laadt en pollt de sub-integraties, cachet en synct readings.

Draait als losse systemd-service (mtd-worker), bewust gescheiden van mtd-core
(heartbeat/WebSocket/statuspagina/OTA-updates). Een hangende of crashende
integratie mag deze worker's eigen loop verstoren, maar nooit de heartbeat,
de lokale statuspagina of het vermogen om een OTA-update te ontvangen — die
blijven in mtd-core draaien, in een apart proces.

Communicatie tussen de twee processen loopt via bestanden i.p.v. gedeeld
geheugen: core zet WS-berichten die de worker raken (config_update,
restart_integration) om in een signaal via signals.py; de worker schrijft zijn
live status (integraties, pending readings, uptime) weg via state.py zodat
core/de statuspagina die kan tonen.
"""
import logging
import sqlite3
import time

from config import ConfigManager
from api import AgentAPIClient
from sync import SyncWorker, DB_PATH
from plugin_manager import PluginManager
from version import VERSION
import signals
import state

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s"
)
logger = logging.getLogger("mtd-worker")

CONFIG_POLL_INTERVAL = 300  # 5 minuten, fallback voor het config_update-signaal
DEFAULT_DELIVERY_INTERVAL = 900  # 15 minuten, fallback als backend geen waarde meestuurt
SYNC_CATCHUP_INTERVAL = 10  # bij backlog niet wachten op delivery_interval maar snel doorpakken
STATE_WRITE_INTERVAL = 2


def _get_pending_readings() -> int | str:
    try:
        con = sqlite3.connect(DB_PATH)
        count = con.execute("SELECT COUNT(*) FROM readings WHERE synced = 0").fetchone()[0]
        con.close()
        return count
    except Exception:
        return "?"


def _get_pending_counts_by_integration() -> dict:
    """Aantal wachtende readings per customer_integration_id."""
    try:
        con = sqlite3.connect(DB_PATH)
        rows = con.execute(
            "SELECT customer_integration_id, COUNT(*) FROM readings WHERE synced = 0 GROUP BY customer_integration_id"
        ).fetchall()
        con.close()
        return {r[0]: r[1] for r in rows}
    except Exception:
        return {}


class Worker:
    def __init__(self):
        self.config = ConfigManager()
        self.api = AgentAPIClient(self.config)
        self.sync = SyncWorker(self.api)
        self.plugins = PluginManager()
        self.integrations = {}
        self._last_poll = {}
        self._start_time = time.time()

    @staticmethod
    def _config_changed(old_cfg: dict, new_cfg: dict) -> bool:
        """Vergelijk config zonder interne/gemuteerde velden (bijv. Enphase '_token')."""
        strip = lambda d: {k: v for k, v in d.items() if not k.startswith("_")}
        return strip(old_cfg) != strip(new_cfg)

    @staticmethod
    def _track_key(cfg: dict) -> str:
        """Sleutel waarop integraties intern worden bijgehouden. Gebruik de stabiele
        'slug' als die beschikbaar is (blijft gelijk over saves heen); val terug op
        'id' voor backends die nog geen slug meesturen. Let op: 'id' zelf (de
        customer_integration_id) blijft altijd naar de integratie doorgegeven voor
        readings/events, ongeacht welke sleutel hier gebruikt wordt."""
        return cfg.get("slug") or cfg["id"]

    def _load_integrations(self, integration_configs: list):
        """Laad, herlaad of stop integraties op basis van config. Raakt alleen
        integraties aan die daadwerkelijk gestopt, nieuw of gewijzigd zijn,
        zodat een save van 1 integratie niet alle andere herstart.

        Een individuele kapotte/onvolledige config-entry mag de rest van de
        batch nooit blokkeren, dus elke entry wordt apart afgehandeld."""
        valid_configs = []
        for cfg in integration_configs:
            if "id" in cfg and "type" in cfg:
                valid_configs.append(cfg)
            else:
                logger.error(f"Integratieconfig overgeslagen, ontbrekend 'id' of 'type': {cfg}")

        new_keys = {self._track_key(cfg) for cfg in valid_configs}

        # Verwijder gestopte integraties
        for key in list(self.integrations.keys()):
            if key not in new_keys:
                logger.info(f"Integratie gestopt: {key}")
                self.integrations[key].close()
                del self.integrations[key]

        # Laad nieuwe of gewijzigde integraties
        for cfg in valid_configs:
            key = self._track_key(cfg)
            iid = cfg["id"]
            try:
                existing = self.integrations.get(key)
                if existing is not None and not self._config_changed(existing.config, cfg):
                    continue  # ongewijzigd, niet herladen

                plugin_name = cfg["type"]
                cls = self.plugins.get_integration_class(plugin_name)
                if cls:
                    if existing is not None:
                        # Sluit open verbindingen (bv. seriële poort) vóórdat de
                        # nieuwe instance dezelfde resource claimt — anders houdt
                        # de oude, niet meer gepolde instance de poort vast tot de
                        # garbage collector hem opruimt, en botst de nieuwe instance
                        # daar bij zijn eerste poll() tegenaan.
                        existing.close()
                    instance = cls(iid, cfg, self.sync, self.api)
                    # Gepauzeerde instanties (enabled=false) blijven geladen zodat ze op de
                    # lokale statuspagina zichtbaar blijven als "gepauzeerd" i.p.v. te
                    # verdwijnen — alleen het daadwerkelijk pollen wordt overgeslagen (zie run()).
                    instance.enabled = cfg.get("enabled", True)
                    self.integrations[key] = instance
                    logger.info(f"Integratie {'bijgewerkt' if existing else 'geladen'}: {cfg.get('name', plugin_name)}"
                                + ("" if instance.enabled else " (gepauzeerd)"))
                else:
                    logger.error(f"Plugin niet gevonden: {plugin_name}")
            except Exception as e:
                logger.error(f"Integratie {key} laden mislukt: {e}")

    def _refresh_config(self):
        """Haal config op van platform en herlaad integraties. Is de backend
        onbereikbaar (bijv. bij opstarten zonder verbinding), val dan terug op de
        laatst bekende integraties uit config.json in plaats van leeg te starten."""
        remote_config = self.api.get_config()
        if remote_config:
            self.config.set("integrations", remote_config.get("integrations", []))
            self.config.set("delivery_interval_seconds", remote_config.get("delivery_interval_seconds", DEFAULT_DELIVERY_INTERVAL))
            self._load_integrations(remote_config.get("integrations", []))
        elif not self.integrations:
            cached = self.config.get("integrations", [])
            if cached:
                logger.warning("Backend onbereikbaar, val terug op laatst bekende integraties uit cache")
                self._load_integrations(cached)

    def _process_signals(self):
        """Verwerk signalen die mtd-core doorzet (WS-berichten die de worker raken).
        Een fout bij 1 signaal mag de rest van de loop nooit blokkeren."""
        for sig in signals.pop_all():
            try:
                if sig["type"] == "config_update":
                    # Het bericht draagt in de praktijk geen "config"-payload mee (ondanks
                    # het gedocumenteerde contract) - het is puur een signaal dat er iets
                    # gewijzigd is. Haal de actuele integraties daarom altijd vers op via
                    # REST i.p.v. te vertrouwen op een mogelijk lege payload.
                    self._refresh_config()
                elif sig["type"] == "restart_integration":
                    iid = sig["payload"].get("integration_id")
                    # self.integrations kan op 'slug' of op 'id' gesleuteld zijn (zie
                    # _track_key); zoek op beide zodat dit werkt ongeacht welke waarde
                    # het platform hier meestuurt.
                    key = iid if iid in self.integrations else next(
                        (k for k, v in self.integrations.items() if v.customer_integration_id == iid), None
                    )
                    if key is not None:
                        self.integrations[key].close()
                        del self.integrations[key]
                        logger.info(f"Integratie herstart: {iid}")
                    self._refresh_config()
            except Exception as e:
                logger.error(f"Fout bij verwerken signaal {sig.get('type')}: {e}")

    def _write_state(self):
        pending_counts = _get_pending_counts_by_integration()
        integrations = []
        for iid, integration in list(self.integrations.items()):
            try:
                last = self._last_poll.get(iid, None)
                integrations.append({
                    "name": integration.name,
                    "type": integration.config.get("type", "?"),
                    "poll_interval": integration.poll_interval,
                    "last_poll": last,
                    "errors": integration._error_count,
                    "recent_errors": list(integration._recent_errors),
                    "pending": pending_counts.get(integration.customer_integration_id, 0),
                    "enabled": getattr(integration, "enabled", True),
                })
            except Exception as e:
                logger.error(f"Fout bij opbouwen status voor {iid}: {e}")

        state.write({
            "version": VERSION,
            "uptime_seconds": time.time() - self._start_time,
            "pending_readings": _get_pending_readings(),
            "integrations": integrations,
        })

    def run(self):
        import api as api_module
        logger.info(f"MTD Worker {VERSION} gestart — API_URL={api_module.API_URL}")
        self._refresh_config()

        last_sync = 0
        last_config_poll = 0
        last_state_write = 0
        sync_backlog = False

        while True:
            now = time.time()

            self._process_signals()

            # Config polling (fallback voor het config_update-signaal)
            if now - last_config_poll >= CONFIG_POLL_INTERVAL:
                try:
                    self._refresh_config()
                except Exception as e:
                    logger.error(f"Fout bij config ophalen: {e}")
                last_config_poll = now

            # Poll integraties op basis van eigen interval — gepauzeerde integraties
            # (enabled=false) blijven geladen voor de statuspagina maar worden overgeslagen.
            for iid, integration in list(self.integrations.items()):
                if not getattr(integration, "enabled", True):
                    continue
                last = self._last_poll.get(iid, 0)
                if now - last >= integration.poll_interval:
                    try:
                        integration.poll()
                    except Exception as e:
                        logger.error(f"Fout in integratie {iid}: {e}")
                        try:
                            integration.report_error(str(e))
                        except Exception as report_e:
                            logger.error(f"Fout bij rapporteren van fout voor {iid}: {report_e}")
                    self._last_poll[iid] = now

            # Sync cache naar platform. Bij een backlog (volle batch verstuurd,
            # mogelijk meer wachtend) meteen doorpakken i.p.v. te wachten op het
            # volle interval, anders loopt een achterstand nooit in.
            sync_interval = self.config.get("delivery_interval_seconds", DEFAULT_DELIVERY_INTERVAL)
            effective_interval = SYNC_CATCHUP_INTERVAL if sync_backlog else sync_interval
            if now - last_sync >= effective_interval:
                try:
                    sync_backlog = self.sync.flush()
                except Exception as e:
                    logger.error(f"Fout bij synchroniseren: {e}")
                    sync_backlog = False
                last_sync = now

            if now - last_state_write >= STATE_WRITE_INTERVAL:
                try:
                    self._write_state()
                except Exception as e:
                    logger.error(f"Fout bij wegschrijven state: {e}")
                last_state_write = now

            time.sleep(1)


if __name__ == "__main__":
    Worker().run()
