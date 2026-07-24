"""GoodWe local UDP plugin — async `goodwe`-library (poort 8899), zelfde
structuur als `plugins/sma_webconnect/plugin.py`: de library is zelf al async,
dus de calls worden rechtstreeks ge-await't in `collect()`, geen
`run_blocking()` nodig.

`_last_known_good` is een instance-attribuut (geen SQLite-cache) — zelfde
conventie als sma_webconnect/solaredge: "laatst bekende goede waarde" hoeft
alleen de levensduur van het proces te overleven, niet een herstart.

Let op: de `goodwe`-library discovert per omvormer-model (ET/EH/DT/XS/…) een
eigen sensorenset via `inverter.sensors()`. SENSOR_TO_METRIC dekt de meest
voorkomende sensor-id's; ontbrekende sensoren worden gewoon overgeslagen door
`_normalize()`."""
import logging
from datetime import datetime, timezone

from core.plugin import Command, DevicePlugin, Reading

log = logging.getLogger("plugin.goodwe")

# sensor_id (goodwe-library) → (metric, unit, direction)
SENSOR_TO_METRIC = {
    "ppv": ("current_power_w", "W", "production"),
    "ppv1": ("pv1_power_w", "W", "production"),
    "vpv1": ("pv1_voltage_v", "V", "production"),
    "ipv1": ("pv1_current_a", "A", "production"),
    "ppv2": ("pv2_power_w", "W", "production"),
    "vpv2": ("pv2_voltage_v", "V", "production"),
    "ipv2": ("pv2_current_a", "A", "production"),
    "pgrid": ("grid_power_w", "W", "production"),
    "fgrid": ("grid_frequency_hz", "Hz", "production"),
    "vgrid": ("grid_voltage_v", "V", "production"),
    "igrid": ("grid_current_a", "A", "production"),
    "temperature": ("inverter_temp_c", "degC", "production"),
    "e_day": ("energy_today_kwh", "kWh", "production"),
    "e_total": ("energy_lifetime_kwh", "kWh", "production"),
    "h_total": ("running_hours_total", "h", "production"),
    "battery_soc": ("battery_soc_pct", "%", "production"),
    "battery_power": ("battery_power_w", "W", "production"),
    "battery_temperature": ("battery_temp_c", "degC", "production"),
    "meter_active_power_total": ("meter_power_w", "W", "import"),
    "house_consumption": ("load_power_w", "W", "consumption"),
}
# "work_mode"/"work_mode_label" hebben geen numerieke waarde en passen niet in
# Reading.value (float) — worden alleen gelogd, net zoals solaredge's inverter_mode.
STATUS_SENSORS = ("work_mode", "work_mode_label")


async def _read_sensors(host: str) -> dict:
    import goodwe

    inverter = await goodwe.connect(host)
    data = await inverter.read_runtime_data()
    return {"values": data}


class GoodwePlugin(DevicePlugin):
    def __init__(self, device_id: str, config: dict):
        super().__init__(device_id, config)
        self._last_known_good: dict | None = None

    @property
    def plugin_id(self) -> str:
        return "goodwe"

    async def collect(self) -> list[Reading]:
        host = self.config.get("host")
        if not host:
            raise RuntimeError("Geen host geconfigureerd voor goodwe")

        try:
            data = await _read_sensors(host)
            self._last_known_good = data
        except (TimeoutError, OSError, ConnectionError) as e:
            log.warning("GoodWe: verbinding met %s mislukt, gebruik laatst bekende waarden: %s", host, e)
            data = self._last_known_good

        if not data:
            return []

        return self._normalize(data)

    def _normalize(self, data: dict) -> list[Reading]:
        timestamp = datetime.now(timezone.utc)
        values = data.get("values") or {}
        readings = []

        for sensor_id, (metric, unit, direction) in SENSOR_TO_METRIC.items():
            value = values.get(sensor_id)
            if value is None:
                continue
            readings.append(Reading(
                device_id=self.device_id, metric=metric, value=float(value),
                unit=unit, timestamp=timestamp, source="goodwe", direction=direction,
            ))

        for status_sensor in STATUS_SENSORS:
            status = values.get(status_sensor)
            if status:
                log.debug("GoodWe %s=%s", status_sensor, status)

        return readings

    async def execute(self, command: Command) -> dict:
        raise NotImplementedError("Actuatie niet ondersteund voor goodwe")

    @staticmethod
    async def test_connection(config: dict) -> dict:
        host = config.get("host")
        if not host:
            raise ValueError("host is verplicht")
        data = await _read_sensors(host)
        values = data.get("values") or {}
        return {
            "Huidig vermogen": f"{values.get('ppv', 0)} W",
            "Vandaag opgewekt": f"{values.get('e_day', 0)} kWh",
        }
