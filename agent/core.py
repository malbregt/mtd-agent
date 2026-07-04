"""MTD Core — heartbeat, WebSocket-verbinding, OTA-updates en de lokale
statuspagina.

Draait als losse systemd-service (mtd-core), bewust minimaal gehouden en
volledig gescheiden van mtd-worker (dat de daadwerkelijke integraties laadt
en pollt). Een integratie die vastloopt of crasht kan de worker-service laten
herstarten zonder ooit de heartbeat, de statuspagina of het vermogen om een
OTA-update te ontvangen te raken - die blijven hier draaien.

Berichten die de worker moeten raken (config_update, restart_integration)
worden lokaal doorgezet via signals.py; core zelf handelt alleen af wat het
zonder de worker kan: heartbeat, OTA-update (install.sh), en test_integration
(gebruikt een eigen PluginManager, onafhankelijk van eventuele actieve
integraties in de worker)."""
import asyncio
import json
import logging
import socket
import subprocess
import threading
import time

from config import ConfigManager
from api import AgentAPIClient
from plugin_manager import PluginManager
from websocket_client import WebSocketClient
from status_server import start_status_server
from version import VERSION
import signals

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s"
)
logger = logging.getLogger("mtd-core")

HEARTBEAT_INTERVAL = 30


def get_local_ip():
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except Exception:
        return "unknown"


class Core:
    def __init__(self):
        self.config = ConfigManager()
        self.api = AgentAPIClient(self.config)
        # Alleen gebruikt voor test_integration - een losstaande, stateless check
        # die geen actieve integratie-objecten uit de worker nodig heeft.
        self.plugins = PluginManager()
        self.update_status = "idle"  # lokale weergave op de statuspagina
        self.update_error = None
        self._start_time = time.time()

    async def _on_ws_message(self, data: dict, ws):
        """Verwerk binnenkomend WebSocket bericht. Een fout in de afhandeling
        van 1 bericht mag de WebSocket-verbinding nooit verbreken, en trage/
        blokkerende verwerking mag nooit de WS-ontvangstlus (heartbeats, acks,
        andere berichten) bevriezen. Daarom draait de eigenlijke afhandeling in
        een aparte thread."""
        if data.get("type") == "test_integration":
            asyncio.create_task(self._handle_test_integration(data, ws))
            return

        try:
            loop = asyncio.get_event_loop()
            await loop.run_in_executor(None, self._handle_ws_message, data)
        except Exception as e:
            logger.error(f"Fout bij verwerken WebSocket bericht ({data.get('type')}): {e}")

    def _handle_ws_message(self, data: dict):
        msg_type = data.get("type")
        logger.info(f"WebSocket bericht: {msg_type}")

        if msg_type == "config_update":
            signals.push("config_update")
        elif msg_type == "restart_integration":
            signals.push("restart_integration", {"integration_id": data.get("integration_id")})
        elif msg_type == "update":
            target = data.get("version")
            logger.info(f"OTA update beschikbaar: {target}")
            self.run_update(target)

    def run_update(self, target_version: str | None):
        """Voer install.sh uit met de gevraagde tag. Kan zowel via WebSocket
        (platform) als via de lokale statuspagina (/api/update) getriggerd
        worden - werkt dus ook als de verbinding met het platform wegvalt,
        zolang iemand fysiek/lokaal bij het apparaat kan."""
        threading.Thread(target=self._run_update, args=(target_version,), daemon=True).start()

    def _run_update(self, target_version):
        """Bij succes eindigt install.sh met 'systemctl restart mtd-core' (en
        'mtd-worker'), wat dit proces killt vóórdat het zelf succes kan
        rapporteren - succes wordt daarom bevestigd via heartbeat-reconciliatie
        op de backend zodra de herstarte core zijn nieuwe agent_version meldt.
        Bij falen stopt install.sh (dankzij 'set -e') vóór die restart, dus de
        oude versie blijft draaien en we kunnen het falen hier nog rapporteren."""
        self.update_status = "updating"
        self.update_error = None
        try:
            cmd = ["bash", "/opt/mtd-agent/install.sh"] + ([target_version] if target_version else [])
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=600)
            if result.returncode != 0:
                logger.error(f"Update mislukt: {result.stderr[-2000:]}")
                self.update_status = "failed"
                self.update_error = result.stderr[-2000:]
                self.api.send_update_result(success=False, version=None, error=result.stderr[-2000:])
        except Exception as e:
            logger.error(f"Update-fout: {e}")
            self.update_status = "failed"
            self.update_error = str(e)
            self.api.send_update_result(success=False, version=None, error=str(e))

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
        logger.info(f"MTD Core {VERSION} gestart")
        start_status_server(self)

        # Registreer bij platform
        device_id = self.api.register()
        if device_id:
            self.config.set("device_id", device_id)

        # WebSocket in aparte thread
        def ws_thread():
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            ws = WebSocketClient(self.config, self._on_ws_message)
            loop.run_until_complete(ws.run())

        t = threading.Thread(target=ws_thread, daemon=True)
        t.start()

        last_heartbeat = 0
        while True:
            now = time.time()

            if now - last_heartbeat >= HEARTBEAT_INTERVAL:
                try:
                    self.api.send_heartbeat(VERSION, get_local_ip())
                except Exception as e:
                    logger.error(f"Fout bij heartbeat: {e}")
                last_heartbeat = now

            time.sleep(1)


if __name__ == "__main__":
    Core().run()
