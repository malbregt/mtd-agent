import asyncio
import json
import logging
import os
import socket
import threading
import time

from config import ConfigManager
from api import AgentAPIClient
from sync import SyncWorker
from plugin_manager import PluginManager
from scanner import scan_network
from websocket_client import WebSocketClient
from status_server import start_status_server

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s"
)
logger = logging.getLogger("mtd-agent")

VERSION = "1.0.0"
HEARTBEAT_INTERVAL = 30
CONFIG_POLL_INTERVAL = 300  # 5 minuten, fallback voor WebSocket
SCAN_INTERVAL = 3600  # elk uur
DEFAULT_DELIVERY_INTERVAL = 900  # 15 minuten, fallback als backend geen waarde meestuurt


def get_local_ip():
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except Exception:
        return "unknown"


class Agent:
    def __init__(self):
        self.config = ConfigManager()
        self.api = AgentAPIClient(self.config)
        self.sync = SyncWorker(self.api)
        self.plugins = PluginManager()
        self.integrations = {}
        self._last_poll = {}

    @staticmethod
    def _config_changed(old_cfg: dict, new_cfg: dict) -> bool:
        """Vergelijk config zonder interne/gemuteerde velden (bijv. Enphase '_token')."""
        strip = lambda d: {k: v for k, v in d.items() if not k.startswith("_")}
        return strip(old_cfg) != strip(new_cfg)

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

        new_ids = {cfg["id"] for cfg in valid_configs}

        # Verwijder gestopte integraties
        for iid in list(self.integrations.keys()):
            if iid not in new_ids:
                logger.info(f"Integratie gestopt: {iid}")
                del self.integrations[iid]

        # Laad nieuwe of gewijzigde integraties
        for cfg in valid_configs:
            iid = cfg["id"]
            try:
                existing = self.integrations.get(iid)
                if existing is not None and not self._config_changed(existing.config, cfg):
                    continue  # ongewijzigd, niet herladen

                plugin_name = cfg["type"]
                cls = self.plugins.get_integration_class(plugin_name)
                if cls:
                    self.integrations[iid] = cls(iid, cfg, self.sync, self.api)
                    logger.info(f"Integratie {'bijgewerkt' if existing else 'geladen'}: {cfg.get('name', plugin_name)}")
                else:
                    logger.error(f"Plugin niet gevonden: {plugin_name}")
            except Exception as e:
                logger.error(f"Integratie {iid} laden mislukt: {e}")

    def _refresh_config(self):
        """Haal config op van platform en herlaad integraties."""
        remote_config = self.api.get_config()
        if remote_config:
            self._load_integrations(remote_config.get("integrations", []))
            self.config.set("delivery_interval_seconds", remote_config.get("delivery_interval_seconds", DEFAULT_DELIVERY_INTERVAL))

    async def _on_ws_message(self, data: dict, ws):
        """Verwerk binnenkomend WebSocket bericht. Een fout in de afhandeling
        van 1 bericht mag de WebSocket-verbinding nooit verbreken."""
        try:
            await self._handle_ws_message(data, ws)
        except Exception as e:
            logger.error(f"Fout bij verwerken WebSocket bericht ({data.get('type')}): {e}")

    async def _handle_ws_message(self, data: dict, ws):
        msg_type = data.get("type")
        logger.info(f"WebSocket bericht: {msg_type}")

        if msg_type == "config_update":
            self._load_integrations(data.get("config", {}).get("integrations", []))

        elif msg_type == "scan":
            results = scan_network()
            self.api.send_scan(results)

        elif msg_type == "restart_integration":
            iid = data.get("integration_id")
            if iid in self.integrations:
                del self.integrations[iid]
                logger.info(f"Integratie herstart: {iid}")
                self._refresh_config()

        elif msg_type == "update":
            logger.info(f"OTA update beschikbaar: {data.get('version')}")
            os.system("/opt/mtd-agent/install.sh")

        elif msg_type == "test_integration":
            asyncio.create_task(self._handle_test_integration(data, ws))

    async def _handle_test_integration(self, data: dict, ws):
        """Test een integratieconfig zonder deze op te slaan en stuur test_result terug."""
        request_id = data.get("request_id")
        integration_id = data.get("integration_id")
        config = data.get("config", {})
        loop = asyncio.get_event_loop()
        start = time.time()

        try:
            cls = self.plugins.get_integration_class(integration_id)
            if not cls:
                raise RuntimeError(f"Onbekende integratie: {integration_id}")
            device = await loop.run_in_executor(None, cls.test_connection, config)
            result = {
                "type": "test_result",
                "request_id": request_id,
                "success": True,
                "response_ms": int((time.time() - start) * 1000),
                "device": device or {},
                "error": None,
            }
        except Exception as e:
            logger.warning(f"Integratietest mislukt ({integration_id}): {e}")
            result = {
                "type": "test_result",
                "request_id": request_id,
                "success": False,
                "response_ms": 0,
                "device": {},
                "error": str(e),
            }

        try:
            await ws.send(json.dumps(result))
        except Exception as e:
            logger.error(f"Kon test_result niet versturen: {e}")

    def run(self):
        logger.info(f"MTD Agent {VERSION} gestart")
        start_status_server(self)

        # Registreer bij platform
        device_id = self.api.register()
        if device_id:
            self.config.set("device_id", device_id)

        # Initiële config ophalen
        self._refresh_config()

        # Initiële netwerkscan
        results = scan_network()
        self.api.send_scan(results)

        # WebSocket in aparte thread
        def ws_thread():
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            ws = WebSocketClient(self.config, self._on_ws_message)
            loop.run_until_complete(ws.run())

        t = threading.Thread(target=ws_thread, daemon=True)
        t.start()

        last_heartbeat = 0
        last_sync = 0
        last_config_poll = 0
        last_scan = 0

        while True:
            now = time.time()

            # Heartbeat
            if now - last_heartbeat >= HEARTBEAT_INTERVAL:
                try:
                    self.api.send_heartbeat(VERSION, get_local_ip())
                except Exception as e:
                    logger.error(f"Fout bij heartbeat: {e}")
                last_heartbeat = now

            # Config polling (fallback voor WebSocket)
            if now - last_config_poll >= CONFIG_POLL_INTERVAL:
                try:
                    self._refresh_config()
                except Exception as e:
                    logger.error(f"Fout bij config ophalen: {e}")
                last_config_poll = now

            # Periodieke netwerkscan
            if now - last_scan >= SCAN_INTERVAL:
                try:
                    results = scan_network()
                    self.api.send_scan(results)
                except Exception as e:
                    logger.error(f"Fout bij netwerkscan: {e}")
                last_scan = now

            # Poll integraties op basis van eigen interval
            for iid, integration in list(self.integrations.items()):
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

            # Sync cache naar platform
            sync_interval = self.config.get("delivery_interval_seconds", DEFAULT_DELIVERY_INTERVAL)
            if now - last_sync >= sync_interval:
                try:
                    self.sync.flush()
                except Exception as e:
                    logger.error(f"Fout bij synchroniseren: {e}")
                last_sync = now

            time.sleep(1)


if __name__ == "__main__":
    agent = Agent()
    agent.run()
