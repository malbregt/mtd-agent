import requests
import logging
from datetime import datetime, timezone
from .base import BaseIntegration

logger = logging.getLogger("homewizard")


class HomeWizardIntegration(BaseIntegration):
    def __init__(self, config: dict, sync):
        super().__init__(config, sync)
        self.host = config.get("host")  # bijv. "192.168.1.50"

    def poll(self):
        if not self.host:
            logger.error("Geen host geconfigureerd voor HomeWizard")
            return

        try:
            resp = requests.get(f"http://{self.host}/api/v1/data", timeout=5)
            resp.raise_for_status()
            data = resp.json()
            timestamp = datetime.now(timezone.utc).isoformat()
            self.sync.store("homewizard_p1", timestamp, data)
            logger.debug(f"HomeWizard data opgehaald: {data.get('active_power_w')}W")
        except requests.RequestException as e:
            logger.warning(f"HomeWizard fout: {e}")
