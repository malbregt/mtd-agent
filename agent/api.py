import requests
import logging
import os

logger = logging.getLogger("api")

API_URL = os.environ.get("MTD_API_URL", "http://192.168.0.109:8000")


class AgentAPIClient:
    def __init__(self, config):
        self.config = config

    @property
    def headers(self):
        return {
            "X-Api-Key": self.config.get("instance_key")
        }

    def register(self):
        """Registreer device bij platform, retourneert device_id."""
        try:
            resp = requests.post(
                f"{API_URL}/agent/register",
                headers=self.headers,
                timeout=10
            )
            if resp.status_code == 200:
                data = resp.json()
                logger.info(f"Geregistreerd als device {data['device_id']}")
                return data["device_id"]
            else:
                logger.error(f"Registratie mislukt: HTTP {resp.status_code}")
        except requests.RequestException as e:
            logger.error(f"Registratie fout: {e}")
        return None

    def get_config(self):
        """Haal config op van platform."""
        try:
            resp = requests.get(
                f"{API_URL}/agent/config",
                headers=self.headers,
                timeout=10
            )
            if resp.status_code == 200:
                return resp.json()
        except requests.RequestException as e:
            logger.warning(f"Config ophalen mislukt: {e}")
        return None

    def send_heartbeat(self, version: str, ip: str):
        """Stuur heartbeat naar platform."""
        try:
            resp = requests.post(
                f"{API_URL}/agent/heartbeat",
                json={"version": version, "ip": ip},
                headers=self.headers,
                timeout=5
            )
            return resp.status_code == 200
        except requests.RequestException:
            return False

    def send_readings(self, readings: list):
        """Stuur batch van readings naar platform."""
        try:
            resp = requests.post(
                f"{API_URL}/agent/readings",
                json=readings,
                headers=self.headers,
                timeout=10
            )
            return resp.status_code in (200, 202)
        except requests.RequestException as e:
            logger.warning(f"Readings sync mislukt: {e}")
            return False

    def send_scan(self, results: list):
        """Stuur netwerkscan resultaten naar platform."""
        try:
            resp = requests.post(
                f"{API_URL}/agent/scan",
                json={"results": results},
                headers=self.headers,
                timeout=10
            )
            return resp.status_code == 200
        except requests.RequestException as e:
            logger.warning(f"Scan upload mislukt: {e}")
            return False

    def send_event(self, integration_id: str, level: str, message: str):
        """Rapporteer fout of event voor een integratie."""
        try:
            requests.post(
                f"{API_URL}/agent/event",
                json={"integration_id": integration_id, "level": level, "message": message},
                headers=self.headers,
                timeout=5
            )
        except requests.RequestException:
            pass
