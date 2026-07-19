"""
Enphase plugin — cloud JWT-login (Enlighten) + lokale Envoy-polling.

Blijft bewust op de synchrone `requests`-library (i.p.v. aiohttp) omdat de
originele logica meerdere sequentiële calls met dezelfde tolerantie voor
deel-fouten heeft (zie agent/integrations/enphase.py) — overzetten naar
aiohttp zou hier meer risico geven dan het oplevert. Alle calls lopen daarom
via run_blocking() zodat de event loop niet blokkeert.

Let op: deze plugin stuurt platte `inverter.{serial}.*`-metrics i.p.v. de
geneste `inverters`-lijst die de oude REST-`/agent/readings`-aanroep gebruikte.
De platform-normalisatie in app/routers/agent.py accepteert beide vormen.
"""
import base64
import json
import logging
import time
from datetime import datetime, timezone

from core.plugin import Command, DevicePlugin, Reading

log = logging.getLogger("plugin.enphase")

ENLIGHTEN_LOGIN_URL = "https://enlighten.enphaseenergy.com/login/login.json"
ENLIGHTEN_TOKEN_URL = "https://entrez.enphaseenergy.com/tokens"
TOKEN_REFRESH_MARGIN = 3600


def _normalize_host(host: str) -> str:
    if host and "://" not in host:
        return f"https://{host}"
    return host


def _decode_token_exp(token: str) -> float:
    try:
        payload_b64 = token.split(".")[1]
        padded = payload_b64 + "=" * (-len(payload_b64) % 4)
        payload = json.loads(base64.urlsafe_b64decode(padded))
        return float(payload.get("exp", 0))
    except Exception:
        return 0


def _get_token(cfg: dict) -> str:
    import requests

    login_resp = requests.post(
        ENLIGHTEN_LOGIN_URL,
        data={"user[email]": cfg.get("username"), "user[password]": cfg.get("password")},
        timeout=10,
    )
    login_resp.raise_for_status()
    session_id = login_resp.json().get("session_id")
    if not session_id:
        raise RuntimeError("Enlighten login mislukt: geen session_id ontvangen")

    token_resp = requests.post(
        ENLIGHTEN_TOKEN_URL,
        json={"session_id": session_id, "serial_num": cfg.get("serial"), "username": cfg.get("username")},
        timeout=10,
    )
    token_resp.raise_for_status()
    token = token_resp.text.strip()
    if not token:
        raise RuntimeError("Enlighten token ophalen mislukt: leeg antwoord")
    return token


def _get_json(url: str, token: str):
    import requests
    resp = requests.get(url, headers={"Authorization": f"Bearer {token}"}, timeout=10, verify=False)
    resp.raise_for_status()
    return resp.json()


class EnphasePlugin(DevicePlugin):
    def __init__(self, device_id: str, config: dict):
        super().__init__(device_id, config)
        self._token = None
        self._token_exp = 0

    @property
    def plugin_id(self) -> str:
        return "enphase"

    def _collect_blocking(self) -> dict:
        import requests

        host = _normalize_host(self.config.get("host"))
        if not host:
            raise RuntimeError("Geen host geconfigureerd voor Enphase")

        if not self._token or self._token_exp - time.time() < TOKEN_REFRESH_MARGIN:
            self._token = _get_token(self.config)
            self._token_exp = _decode_token_exp(self._token)
            log.info("Enphase JWT ververst, geldig tot %s",
                      datetime.fromtimestamp(self._token_exp, tz=timezone.utc).isoformat())

        data = _get_json(f"{host}/api/v1/production", self._token)
        if not isinstance(data, dict):
            raise RuntimeError(f"Onverwacht antwoord van Envoy: {data}")

        try:
            production = _get_json(f"{host}/production.json", self._token)
            inv = next((p for p in production.get("production", []) if p.get("type") == "inverters"), None)
            data["inverters_active"] = inv.get("activeCount") if inv else None
        except (requests.RequestException, RuntimeError) as e:
            log.warning("Enphase actieve-omvormers-telling ophalen mislukt: %s", e)

        energy_by_serial = {}
        try:
            raw = _get_json(f"{host}/ivp/pdm/device_data", self._token)
            for key, device in raw.items():
                if key in ("deviceCount", "deviceDataLimit") or not isinstance(device, dict):
                    continue
                if device.get("devName") != "pcu" or not device.get("active", True):
                    continue
                serial = device.get("sn")
                channels = device.get("channels")
                if not serial or not channels:
                    continue
                channel = channels[0]
                joules = (channel.get("lifetime") or {}).get("joulesProduced")
                today_wh = (channel.get("wattHours") or {}).get("today")
                energy_by_serial[serial] = {
                    "lifetime_kwh": round(joules / 3600 / 1000, 3) if joules is not None else None,
                    "today_kwh": round(today_wh / 1000, 3) if today_wh is not None else None,
                }
        except (requests.RequestException, RuntimeError) as e:
            log.warning("Enphase omvormer lifetime/vandaag-energie ophalen mislukt: %s", e)

        try:
            inverters = _get_json(f"{host}/api/v1/production/inverters", self._token)
            data["inverters"] = [
                {"serial": inv.get("serialNumber"), "watts": inv.get("lastReportWatts"),
                 **energy_by_serial.get(inv.get("serialNumber"), {})}
                for inv in inverters
                if inv.get("serialNumber") and inv.get("lastReportWatts") is not None
            ]
        except (requests.RequestException, RuntimeError) as e:
            log.warning("Enphase omvormer-lijst ophalen mislukt: %s", e)

        return data

    async def collect(self) -> list[Reading]:
        import requests
        try:
            data = await self.run_blocking(self._collect_blocking)
        except (requests.RequestException, RuntimeError):
            # Token kan door de Envoy zijn afgekeurd terwijl hij er lokaal nog
            # geldig uitzag; forceer een verse token bij de volgende poll.
            self._token = None
            self._token_exp = 0
            raise

        timestamp = datetime.now(timezone.utc)
        readings = [
            Reading(device_id=self.device_id, metric=key, value=value, unit="",
                    timestamp=timestamp, source="enphase", direction="production")
            for key, value in data.items()
            if isinstance(value, (int, float))
        ]
        for inv in data.get("inverters", []):
            serial = inv.get("serial")
            if not serial:
                continue
            for suffix in ("watts", "lifetime_kwh", "today_kwh"):
                value = inv.get(suffix)
                if value is not None:
                    readings.append(Reading(
                        device_id=self.device_id, metric=f"inverter.{serial}.{suffix}", value=value,
                        unit="", timestamp=timestamp, source="enphase", direction="production",
                    ))
        return readings

    async def execute(self, command: Command) -> dict:
        raise NotImplementedError("Actuatie niet ondersteund voor enphase")

    @staticmethod
    def test_connection(config: dict) -> dict:
        host = _normalize_host(config.get("host"))
        if not all([host, config.get("username"), config.get("password"), config.get("serial")]):
            raise ValueError("Host, gebruikersnaam, wachtwoord en serienummer zijn verplicht")
        token = _get_token(config)
        data = _get_json(f"{host}/api/v1/production", token)
        return {
            "Totale opwek": f"{data.get('wattHoursLifetime', 0) / 1000:.1f} kWh",
            "Huidig vermogen": f"{data.get('wattsNow', 0)} W",
        }
