import requests
import logging
from datetime import datetime, timezone
from .base import BaseIntegration

logger = logging.getLogger("enphase")


class EnphaseIntegration(BaseIntegration):
    def poll(self):
        host = self.config.get("host", "envoy.local")

        try:
            resp = requests.get(f"http://{host}/api/v1/production", timeout=5)
            resp.raise_for_status()
            data = resp.json()
            timestamp = datetime.now(timezone.utc).isoformat()
            self.sync.store(self.integration_id, timestamp, data)
            self.report_ok()
            logger.debug(f"Enphase: {data.get('wattsNow')}W")
        except requests.RequestException as e:
            logger.warning(f"Enphase fout: {e}")
            self.report_error(str(e))
