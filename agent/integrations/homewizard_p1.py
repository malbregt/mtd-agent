import requests
import logging
from datetime import datetime, timezone
import sys, os
sys.path.insert(0, os.path.dirname(__file__))
from base import BaseIntegration

logger = logging.getLogger("homewizard_p1")


class HomewizardP1Integration(BaseIntegration):
    @staticmethod
    def test_connection(config: dict) -> dict:
        host = config.get("host")
        if not host:
            raise ValueError("Host is verplicht")
        host = BaseIntegration.normalize_host(host)

        try:
            info_resp = requests.get(f"{host}/api", timeout=5)
            info_resp.raise_for_status()
            info = info_resp.json()

            data_resp = requests.get(f"{host}/api/v1/data", timeout=5)
            data_resp.raise_for_status()
            data = data_resp.json()
        except requests.RequestException as e:
            raise RuntimeError(f"Kan geen verbinding maken met {host}: {e}")

        return {
            "product_name": info.get("product_name"),
            "product_type": info.get("product_type"),
            "serial": info.get("serial"),
            "firmware_version": info.get("firmware_version"),
            "api_version": info.get("api_version"),
            "active_power_w": data.get("active_power_w"),
        }

    def poll(self):
        host = self.config.get("config", {}).get("host") or self.config.get("host")
        if not host:
            logger.error("Geen host geconfigureerd voor HomeWizard")
            return
        host = self.normalize_host(host)

        try:
            resp = requests.get(f"{host}/api/v1/data", timeout=5)
            resp.raise_for_status()
            data = resp.json()
            timestamp = datetime.now(timezone.utc).isoformat()
            self.sync.store(self.integration_id, timestamp, data, self.customer_integration_id)
            self.report_ok()
            logger.debug(f"HomeWizard: {data.get('active_power_w')}W")
        except requests.RequestException as e:
            logger.warning(f"HomeWizard fout: {e}")
            self.report_error(str(e))
