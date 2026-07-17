"""
P1 Serial integratie — leest de P1-poort van de slimme meter rechtstreeks uit
via een USB(-seriële) kabel, zonder tussenkomst van een HomeWizard.

De P1-poort stuurt elke seconde (DSMR 4/5) een ASCII-telegram met alle
meterstanden. Dit plugin opent de seriële poort, verzamelt één volledig
telegram (van "/" tot en met de "!CRC" afsluitregel), controleert de CRC16
en zet de relevante OBIS-referenties om naar dezelfde velden als de
HomeWizard P1-integratie, zodat de rest van de pijplijn (backend-normalisatie,
meter-aanmaak) ongewijzigd hergebruikt kan worden.

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
OBIS_IMPORT_T1 = re.compile(r"1-0:1\.8\.1\((\d+\.\d+)\*kWh\)")
OBIS_IMPORT_T2 = re.compile(r"1-0:1\.8\.2\((\d+\.\d+)\*kWh\)")
OBIS_EXPORT_T1 = re.compile(r"1-0:2\.8\.1\((\d+\.\d+)\*kWh\)")
OBIS_EXPORT_T2 = re.compile(r"1-0:2\.8\.2\((\d+\.\d+)\*kWh\)")
OBIS_POWER_DELIVERED = re.compile(r"1-0:1\.7\.0\((\d+\.\d+)\*kW\)")
OBIS_POWER_RECEIVED = re.compile(r"1-0:2\.7\.0\((\d+\.\d+)\*kW\)")
# Gas staat achter een tijdstempel op dezelfde OBIS-regel, bv:
# 0-1:24.2.1(230101120000W)(01234.567*m3)
OBIS_GAS = re.compile(r"0-1:24\.2\.1\([^)]*\)\((\d+\.\d+)\*m3\)")


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

    return {
        "total_power_import_t1_kwh": import_t1,
        "total_power_import_t2_kwh": import_t2,
        "total_power_export_t1_kwh": export_t1,
        "total_power_export_t2_kwh": export_t2,
        "active_power_w": active_power_w,
        "total_gas_m3": gas_m3,
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
            "electricity_import_kwh": round(
                (data.get("total_power_import_t1_kwh") or 0)
                + (data.get("total_power_import_t2_kwh") or 0), 3),
            "electricity_export_kwh": round(
                (data.get("total_power_export_t1_kwh") or 0)
                + (data.get("total_power_export_t2_kwh") or 0), 3),
            "active_power_w": data.get("active_power_w"),
            "gas_m3": data.get("total_gas_m3"),
        }

    def poll(self):
        try:
            if self._serial is None or not self._serial.is_open:
                self._serial = self._open_serial()

            raw = _read_telegram(self._serial)
            data = parse_telegram(raw)
            timestamp = datetime.now(timezone.utc).isoformat()
            self.store_reading(timestamp, data)
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
