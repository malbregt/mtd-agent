import requests
import logging
import os

logger = logging.getLogger("api")

API_URL = os.environ.get("MTD_API_URL", "https://api.mijnthuisdata.nl")


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

    def send_heartbeat(self, version: str, ip: str) -> str | None:
        """Stuur heartbeat naar platform, retourneert de laatst beschikbare
        agent-versie zoals bekend bij het platform (None bij falen/onbekend)."""
        try:
            resp = requests.post(
                f"{API_URL}/agent/heartbeat",
                json={"agent_version": version, "ip_address": ip},
                headers=self.headers,
                timeout=5
            )
            if resp.status_code == 200:
                return resp.json().get("latest_version")
        except requests.RequestException:
            pass
        return None

    def send_update_result(self, success: bool, version: str | None, error: str | None):
        """Rapporteer een mislukte OTA-update. Bij succes herstart install.sh dit proces
        vóórdat succes zelf gemeld kan worden — dat wordt bevestigd via de eerstvolgende
        heartbeat in plaats van hier."""
        try:
            resp = requests.post(
                f"{API_URL}/agent/update-result",
                json={"success": success, "version": version, "error": error},
                headers=self.headers,
                timeout=10,
            )
            return resp.status_code == 200
        except requests.RequestException as e:
            logger.warning(f"Update-result rapporteren mislukt: {e}")
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
            if resp.status_code in (200, 202):
                return True
            # Serverfout (bv. 401/422/500) gaf voorheen geen enkele logregel — daardoor
            # was een mislukte sync niet te onderscheiden van "nog niet geprobeerd" in
            # de agent-logs. Body ingekort want kan een volledige HTML-errorpagina zijn.
            logger.warning(f"Readings sync mislukt: HTTP {resp.status_code} — {resp.text[:300]}")
            return False
        except requests.RequestException as e:
            logger.warning(f"Readings sync mislukt: {e}")
            return False

    def send_event(self, integration_id: str, status: str, error_message: str | None = None,
                   customer_integration_id: str | None = None):
        """Rapporteer gezondheidsstatus (healthy/error/unknown) van een integratie."""
        try:
            resp = requests.post(
                f"{API_URL}/agent/event",
                json={
                    "integration_id": integration_id,
                    "status": status,
                    "error_message": error_message,
                    "customer_integration_id": customer_integration_id,
                },
                headers=self.headers,
                timeout=5
            )
            if resp.status_code >= 400:
                logger.warning(f"Event rapporteren mislukt ({integration_id}): HTTP {resp.status_code} {resp.text[:200]}")
        except requests.RequestException as e:
            logger.warning(f"Event rapporteren mislukt ({integration_id}): {e}")
