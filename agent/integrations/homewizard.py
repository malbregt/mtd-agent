import requests
import logging
from datetime import datetime, timezone
import sys, os
sys.path.insert(0, os.path.dirname(__file__))
from base import BaseIntegration

logger = logging.getLogger("homewizard")


class HomewizardIntegration(BaseIntegration):
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
