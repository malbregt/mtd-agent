import requests
import logging
from datetime import datetime, timezone
import sys, os
sys.path.insert(0, os.path.dirname(__file__))
from base import BaseIntegration

logger = logging.getLogger("homewizard_water")

class HomewizardWaterIntegration(BaseIntegration):
    def poll(self):
        host = self.config.get("config", {}).get("host") or self.config.get("host")
        if not host:
            logger.error("Geen host geconfigureerd voor HomeWizard Watermeter")
            return

        try:
            resp = requests.get(f"http://{host}/api/v1/data", timeout=5)
            resp.raise_for_status()
            data = resp.json()
            timestamp = datetime.now(timezone.utc).isoformat()
            self.sync.store(self.integration_id, timestamp, data)
            self.report_ok()
            logger.debug(f"Watermeter: {data.get('active_liter_lpm')} l/min, totaal {data.g>
        except requests.RequestException as e:
            logger.warning(f"Watermeter fout: {e}")
            self.report_error(str(e))
