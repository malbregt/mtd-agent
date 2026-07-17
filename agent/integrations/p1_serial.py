"""
P1 Serial integratie — leest de P1-poort van de slimme meter rechtstreeks uit
via een USB(-seriële) kabel, zonder tussenkomst van een HomeWizard.

De P1-poort stuurt elke seconde (DSMR 4/5) een ASCII-telegram met alle
meterstanden. Dit plugin opent de seriële poort, verzamelt één volledig
telegram (van "/" tot en met de "!CRC" afsluitregel), controleert de CRC16
en zet de relevante OBIS-referenties om naar afzonderlijke velden — inclusief
verbruik/teruglevering per tarief (T1/T2), actief tarief, en spanning/stroom/
vermogen per fase (L1/L2/L3, indien de meter dat ondersteunt). Backend-kant
in app/integrations/p1_serial.py zet dit om naar losse meters (zie
_normalize_integration in app/routers/agent.py).

Config:
  port      seriële poort, bv. "/dev/ttyUSB0" (default) of "/dev/ttyAMA0"
  baudrate  115200 voor DSMR 4/5 (default), 9600 voor oudere DSMR 2/3-meters
"""
import logging
import re
import sys, os
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(__file__))
from base import BaseIntegration

logger = logging.getLogger("p1_serial")

DEFAULT_PORT = "/dev/ttyUSB0"
DEFAULT_BAUDRATE = 115200
TELEGRAM_TIMEOUT = 15  # seconden — DSMR-meters sturen elke ~1s een telegram

# OBIS-referenties die we nodig hebben, per DSMR-versie grotendeels gelijk.
# Decimaalgetal flexibel (\d+(?:\.\d+)?) i.p.v. een verplichte punt, sommige
# velden (met name stroom per fase) komen soms zonder decimalen door.
_NUM = r"(\d+(?:\.\d+)?)"
OBIS_IMPORT_T1 = re.compile(rf"1-0:1\.8\.1\({_NUM}\*kWh\)")
OBIS_IMPORT_T2 = re.compile(rf"1-0:1\.8\.2\({_NUM}\*kWh\)")
OBIS_EXPORT_T1 = re.compile(rf"1-0:2\.8\.1\({_NUM}\*kWh\)")
OBIS_EXPORT_T2 = re.compile(rf"1-0:2\.8\.2\({_NUM}\*kWh\)")
OBIS_TARIFF = re.compile(r"0-0:96\.14\.0\((\d+)\)")
OBIS_POWER_DELIVERED = re.compile(rf"1-0:1\.7\.0\({_NUM}\*kW\)")
OBIS_POWER_RECEIVED = re.compile(rf"1-0:2\.7\.0\({_NUM}\*kW\)")
# Gas staat achter een tijdstempel op dezelfde OBIS-regel, bv:
# 0-1:24.2.1(230101120000W)(01234.567*m3)
OBIS_GAS = re.compile(rf"0-1:24\.2\.1\([^)]*\)\({_NUM}\*m3\)")
# Spanning/stroom/vermogen per fase — alleen aanwezig als de meter dit ondersteunt
# (eenfase-aansluitingen hebben doorgaans alleen L1, driefase-aansluitingen alle 3).
OBIS_VOLTAGE_L1 = re.compile(rf"1-0:32\.7\.0\({_NUM}\*V\)")
OBIS_VOLTAGE_L2 = re.compile(rf"1-0:52\.7\.0\({_NUM}\*V\)")
OBIS_VOLTAGE_L3 = re.compile(rf"1-0:72\.7\.0\({_NUM}\*V\)")
OBIS_CURRENT_L1 = re.compile(rf"1-0:31\.7\.0\({_NUM}\*A\)")
OBIS_CURRENT_L2 = re.compile(rf"1-0:51\.7\.0\({_NUM}\*A\)")
OBIS_CURRENT_L3 = re.compile(rf"1-0:71\.7\.0\({_NUM}\*A\)")
OBIS_POWER_POS_L1 = re.compile(rf"1-0:21\.7\.0\({_NUM}\*kW\)")
OBIS_POWER_NEG_L1 = re.compile(rf"1-0:22\.7\.0\({_NUM}\*kW\)")
OBIS_POWER_POS_L2 = re.compile(rf"1-0:41\.7\.0\({_NUM}\*kW\)")
OBIS_POWER_NEG_L2 = re.compile(rf"1-0:42\.7\.0\({_NUM}\*kW\)")
OBIS_POWER_POS_L3 = re.compile(rf"1-0:61\.7\.0\({_NUM}\*kW\)")
OBIS_POWER_NEG_L3 = re.compile(rf"1-0:62\.7\.0\({_NUM}\*kW\)")


def _crc16(data: bytes) -> int:
    """CRC16/ARC (poly 0xA001, init 0x0000) zoals gebruikt in het DSMR-telegram."""
    crc = 0x0000
    for byte in data:
        crc ^= byte
        for _ in range(8):
            if crc & 0x0001:
                crc = (crc >> 1) ^ 0xA001
            else:
                crc >>= 1
    return crc


def parse_telegram(raw: bytes) -> dict:
    """Parseer een compleet DSMR-telegram (inclusief CRC-afsluitregel) naar de
    HomeWizard-achtige velden. Gooit ValueError bij een ongeldige CRC of als er
    geen bruikbare velden gevonden zijn."""
    text = raw.decode("ascii", errors="replace")

    bang_idx = text.rfind("!")
    if bang_idx == -1:
        raise ValueError("Telegram zonder CRC-afsluiting ('!') ontvangen")

    # CRC wordt berekend over alles t/m en met de "!", exclusief de hex-waarde erna.
    crc_match = re.match(r"!([0-9A-Fa-f]{4})", text[bang_idx:])
    if crc_match:
        expected = int(crc_match.group(1), 16)
        actual = _crc16(raw[:bang_idx + 1])
        if expected != actual:
            raise ValueError(f"CRC-fout: verwacht {expected:04X}, berekend {actual:04X}")
    else:
        logger.debug("Geen CRC-checksum aanwezig in telegram (oudere DSMR-versie), overslaan")

    def _find(pattern):
        m = pattern.search(text)
        return float(m.group(1)) if m else None

    import_t1 = _find(OBIS_IMPORT_T1)
    import_t2 = _find(OBIS_IMPORT_T2)
    export_t1 = _find(OBIS_EXPORT_T1)
    export_t2 = _find(OBIS_EXPORT_T2)
    delivered_kw = _find(OBIS_POWER_DELIVERED)
    received_kw = _find(OBIS_POWER_RECEIVED)
    gas_m3 = _find(OBIS_GAS)

    if import_t1 is None and delivered_kw is None:
        raise ValueError("Geen bruikbare meterstanden gevonden in telegram")

    active_power_w = None
    if delivered_kw is not None or received_kw is not None:
        active_power_w = round(((delivered_kw or 0.0) - (received_kw or 0.0)) * 1000)

    def _net_power_w(pos_pattern, neg_pattern):
        pos = _find(pos_pattern)
        neg = _find(neg_pattern)
        if pos is None and neg is None:
            return None
        return round(((pos or 0.0) - (neg or 0.0)) * 1000)

    tariff_match = OBIS_TARIFF.search(text)

    return {
        "total_power_import_t1_kwh": import_t1,
        "total_power_import_t2_kwh": import_t2,
        "total_power_export_t1_kwh": export_t1,
        "total_power_export_t2_kwh": export_t2,
        "active_power_w": active_power_w,
        "active_tariff": int(tariff_match.group(1)) if tariff_match else None,
        "total_gas_m3": gas_m3,
        "voltage_l1_v": _find(OBIS_VOLTAGE_L1),
        "voltage_l2_v": _find(OBIS_VOLTAGE_L2),
        "voltage_l3_v": _find(OBIS_VOLTAGE_L3),
        "current_l1_a": _find(OBIS_CURRENT_L1),
        "current_l2_a": _find(OBIS_CURRENT_L2),
        "current_l3_a": _find(OBIS_CURRENT_L3),
        "power_l1_w": _net_power_w(OBIS_POWER_POS_L1, OBIS_POWER_NEG_L1),
        "power_l2_w": _net_power_w(OBIS_POWER_POS_L2, OBIS_POWER_NEG_L2),
        "power_l3_w": _net_power_w(OBIS_POWER_POS_L3, OBIS_POWER_NEG_L3),
    }


def _read_telegram(ser) -> bytes:
    """Lees van de seriële poort tot een compleet telegram ('/' ... '!CRC') binnen is."""
    buf = bytearray()
    collecting = False
    while True:
        line = ser.readline()
        if not line:
            raise TimeoutError("Geen data ontvangen van P1-poort (time-out)")

        if not collecting:
            if line.startswith(b"/"):
                collecting = True
                buf = bytearray(line)
            continue

        buf.extend(line)
        if line.startswith(b"!"):
            return bytes(buf)


class P1SerialIntegration(BaseIntegration):
    def __init__(self, integration_id, config, sync, api_client):
        super().__init__(integration_id, config, sync, api_client)
        self._serial = None

    def _get_serial_config(self):
        cfg = self.config.get("config", self.config)
        port = cfg.get("port") or DEFAULT_PORT
        baudrate = int(cfg.get("baudrate") or DEFAULT_BAUDRATE)
        return port, baudrate

    def _open_serial(self):
        import serial  # pyserial — alleen nodig als deze integratie actief is

        port, baudrate = self._get_serial_config()
        return serial.Serial(
            port=port,
            baudrate=baudrate,
            bytesize=serial.EIGHTBITS if baudrate == 115200 else serial.SEVENBITS,
            parity=serial.PARITY_NONE if baudrate == 115200 else serial.PARITY_EVEN,
            stopbits=serial.STOPBITS_ONE,
            timeout=TELEGRAM_TIMEOUT,
            xonxoff=False,
            rtscts=False,
        )

    @staticmethod
    def test_connection(config: dict) -> dict:
        import serial

        port = config.get("port") or DEFAULT_PORT
        baudrate = int(config.get("baudrate") or DEFAULT_BAUDRATE)

        try:
            ser = serial.Serial(
                port=port,
                baudrate=baudrate,
                bytesize=serial.EIGHTBITS if baudrate == 115200 else serial.SEVENBITS,
                parity=serial.PARITY_NONE if baudrate == 115200 else serial.PARITY_EVEN,
                stopbits=serial.STOPBITS_ONE,
                timeout=TELEGRAM_TIMEOUT,
            )
        except serial.SerialException as e:
            raise RuntimeError(f"Kan poort {port} niet openen: {e}")

        try:
            raw = _read_telegram(ser)
            data = parse_telegram(raw)
        except (TimeoutError, ValueError) as e:
            raise RuntimeError(str(e))
        finally:
            ser.close()

        return {
            "port": port,
            "baudrate": baudrate,
            "electricity_import_t1_kwh": data.get("total_power_import_t1_kwh"),
            "electricity_import_t2_kwh": data.get("total_power_import_t2_kwh"),
            "electricity_export_t1_kwh": data.get("total_power_export_t1_kwh"),
            "electricity_export_t2_kwh": data.get("total_power_export_t2_kwh"),
            "active_tariff": data.get("active_tariff"),
            "active_power_w": data.get("active_power_w"),
            "gas_m3": data.get("total_gas_m3"),
            "voltage_l1_v": data.get("voltage_l1_v"),
            "voltage_l2_v": data.get("voltage_l2_v"),
            "voltage_l3_v": data.get("voltage_l3_v"),
        }

    def poll(self):
        try:
            if self._serial is None or not self._serial.is_open:
                self._serial = self._open_serial()

            # Wacht op minstens één telegram, en trek daarna meteen ook alles leeg wat
            # er intussen al bij lag (bv. na een trage vorige cyclus) — anders raakt de
            # kleine seriële OS-buffer vol en gaat er stilzwijgend data verloren i.p.v.
            # dat we gewoon alles verwerken wat de meter al gestuurd heeft.
            raw = _read_telegram(self._serial)
            data = parse_telegram(raw)
            timestamp = datetime.now(timezone.utc).isoformat()
            self.store_reading(timestamp, data)

            while self._serial.in_waiting > 0:
                raw = _read_telegram(self._serial)
                data = parse_telegram(raw)
                self.store_reading(datetime.now(timezone.utc).isoformat(), data)

            self.report_ok()
            logger.debug(f"P1 serieel: {data.get('active_power_w')}W")
        except Exception as e:
            logger.warning(f"P1 serieel fout: {e}")
            self.report_error(str(e))
            if self._serial is not None:
                try:
                    self._serial.close()
                except Exception:
                    pass
                self._serial = None
