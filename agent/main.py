import asyncio
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
SYNC_INTERVAL = 60
CONFIG_POLL_INTERVAL = 60
SCAN_INTERVAL = 3600  # elk uur


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

    def _load_integrations(self, integration_configs: list):
        """Laad of herlaad integraties op basis van config."""
        new_ids = {i["integration_id"] for i in integration_configs}

        # Verwijder gestopte integraties
        for iid in list(self.integrations.keys()):
            if iid not in new_ids:
                logger.info(f"Integratie gestopt: {iid}")
                del self.integrations[iid]

        # Laad nieuwe integraties
        for cfg in integration_configs:
            iid = cfg["integration_id"]
            if iid not in self.integrations:
                plugin_name = cfg["type"]
                cls = self.plugins.get_integration_class(plugin_name)
                if cls:
                    self.integrations[iid] = cls(iid, cfg, self.sync, self.api)
                    logger.info(f"Integratie geladen: {cfg.get('name', plugin_name)}")
                else:
                    logger.error(f"Plugin niet gevonden: {plugin_name}")

    def _refresh_config(self):
        """Haal config op van platform en herlaad integraties."""
        remote_config = self.api.get_config()
        if remote_config:
            self._load_integrations(remote_config.get("integrations", []))

    async def _on_ws_message(self, data: dict):
        """Verwerk binnenkomend WebSocket bericht."""
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
                self.api.send_heartbeat(VERSION, get_local_ip())
                last_heartbeat = now

            # Config polling (fallback voor WebSocket)
            if now - last_config_poll >= CONFIG_POLL_INTERVAL:
                self._refresh_config()
                last_config_poll = now

            # Periodieke netwerkscan
            if now - last_scan >= SCAN_INTERVAL:
                results = scan_network()
                self.api.send_scan(results)
                last_scan = now

            # Poll integraties op basis van eigen interval
            for iid, integration in list(self.integrations.items()):
                last = self._last_poll.get(iid, 0)
                if now - last >= integration.poll_interval:
                    try:
                        integration.poll()
                    except Exception as e:
                        logger.error(f"Fout in integratie {iid}: {e}")
                        integration.report_error(str(e))
                    self._last_poll[iid] = now

            # Sync cache naar platform
            if now - last_sync >= SYNC_INTERVAL:
                self.sync.flush()
                last_sync = now

            time.sleep(1)


if __name__ == "__main__":
    agent = Agent()
    agent.run()
