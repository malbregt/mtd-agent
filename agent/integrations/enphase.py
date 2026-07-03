import requests
import urllib3
import logging
import base64
import json
import time
from datetime import datetime, timezone
import sys, os
sys.path.insert(0, os.path.dirname(__file__))
from base import BaseIntegration

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

logger = logging.getLogger("enphase")

ENLIGHTEN_LOGIN_URL = "https://enlighten.enphaseenergy.com/login/login.json"
ENLIGHTEN_TOKEN_URL = "https://entrez.enphaseenergy.com/tokens"
TOKEN_REFRESH_MARGIN = 3600  # ververs als er nog minder dan dit aantal seconden geldigheid over is


class EnphaseIntegration(BaseIntegration):
    @staticmethod
    def _decode_token_exp(token: str) -> float:
        """Lees de 'exp' claim uit de JWT payload, zonder signature-verificatie."""
        try:
            payload_b64 = token.split(".")[1]
            padded = payload_b64 + "=" * (-len(payload_b64) % 4)
            payload = json.loads(base64.urlsafe_b64decode(padded))
            return float(payload.get("exp", 0))
        except Exception:
            # Onbekende expiry: behandel als meteen verlopen zodat we niet blijven hangen op een dode token
            return 0

    @staticmethod
    def _get_token(config: dict) -> str:
        """Haal JWT token op via Enphase Enlighten cloud voor lokale gateway auth."""
        username = config.get("username")
        password = config.get("password")
        serial = config.get("serial")

        login_resp = requests.post(
            ENLIGHTEN_LOGIN_URL,
            data={"user[email]": username, "user[password]": password},
            timeout=10,
        )
        login_resp.raise_for_status()
        session_id = login_resp.json().get("session_id")
        if not session_id:
            raise RuntimeError("Enlighten login mislukt: geen session_id ontvangen")

        token_resp = requests.post(
            ENLIGHTEN_TOKEN_URL,
            json={"session_id": session_id, "serial_num": serial, "username": username},
            timeout=10,
        )
        token_resp.raise_for_status()
        token = token_resp.text.strip()
        if not token:
            raise RuntimeError("Enlighten token ophalen mislukt: leeg antwoord")
        return token

    @staticmethod
    def _fetch_production(host: str, token: str) -> dict:
        resp = requests.get(
            f"{host}/api/v1/production",
            headers={"Authorization": f"Bearer {token}"},
            timeout=10,
            verify=False,
        )
        resp.raise_for_status()
        data = resp.json()
        if not isinstance(data, dict):
            # De Envoy geeft bij bv. een verlopen/ongeldig token HTTP 200 met een
            # foutobject terug i.p.v. een error-statuscode, dus valideer de vorm.
            raise RuntimeError(f"Onverwacht antwoord van Envoy (geen productiedata): {data}")
        return data

    @staticmethod
    def test_connection(config: dict) -> dict:
        host = BaseIntegration.normalize_host(config.get("host"), default_scheme="https")
        username = config.get("username")
        password = config.get("password")
        serial = config.get("serial")
        if not all([host, username, password, serial]):
            raise ValueError("Host, gebruikersnaam, wachtwoord en serienummer zijn verplicht")

        token = EnphaseIntegration._get_token(config)
        data = EnphaseIntegration._fetch_production(host, token)

        return {
            "Totale opwek": f"{data.get('wattHoursLifetime', 0) / 1000:.1f} kWh",
            "Huidig vermogen": f"{data.get('wattsNow', 0)} W",
        }

    def poll(self):
        cfg = self.config.get("config", self.config)
        host = self.normalize_host(cfg.get("host"), default_scheme="https")
        if not host:
            logger.error("Geen host geconfigureerd voor Enphase")
            return

        try:
            token = cfg.get("_token")
            token_exp = cfg.get("_token_exp", 0)
            if not token or token_exp - time.time() < TOKEN_REFRESH_MARGIN:
                token = self._get_token(cfg)
                token_exp = self._decode_token_exp(token)
                cfg["_token"] = token
                cfg["_token_exp"] = token_exp
                logger.info(f"Enphase JWT ververst, geldig tot {datetime.fromtimestamp(token_exp, tz=timezone.utc).isoformat()}")

            data = self._fetch_production(host, token)
            timestamp = datetime.now(timezone.utc).isoformat()
            self.sync.store(self.integration_id, timestamp, data, self.customer_integration_id)
            self.report_ok()
            logger.debug(f"Enphase: {data.get('wattsNow')}W")
        except (requests.RequestException, RuntimeError) as e:
            logger.warning(f"Enphase fout: {e}")
            # Token kan door de Envoy zijn afgekeurd terwijl hij er lokaal nog geldig uitzag;
            # forceer een verse token bij de volgende poll i.p.v. te blijven hangen op een dode token.
            cfg.pop("_token", None)
            cfg.pop("_token_exp", None)
            self.report_error(str(e))
