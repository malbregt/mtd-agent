"""
P1 Serial integratie — leest de P1-poort van de slimme meter rechtstreeks uit
via een USB(-seriële) kabel, zonder tussenkomst van een HomeWizard.

Bewust géén kennis van welke OBIS-velden "belangrijk" zijn: dit plugin opent
de seriële poort, verzamelt één volledig telegram (van "/" tot en met de
"!CRC" afsluitregel), controleert de CRC16, en zet ELKE OBIS-regel 1-op-1 om
naar een ruwe {obis_code: waarde(n)} dict — ongefilterd en zonder berekeningen.
Interpretatie (welke velden een meter worden, tarief-scheiding, nette namen,
eenheid-afleiding, eventuele afgeleide waarden) gebeurt bewust pas op het
platform (zie app/integrations/p1_serial.py), niet hier. Zo blijft dit plugin
werken als een meter een ander of groter veldenpakket blootgeeft dan verwacht
(nieuwe DSMR-versie, ander merk, extra M-Bus-kanaal, etc.) — er hoeft dan
alleen aan de platformkant iets bij, niet op elk Pi'tje in het veld.

Config:
  port      seriële poort, bv. "/dev/ttyUSB0" (default) of "/dev/ttyAMA0"
  baudrate  115200 voor DSMR 4/5 (default), 9600 voor oudere DSMR 2/3-meters
"""
import fcntl
import logging
import re
import sys, os
import time
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(__file__))
from base import BaseIntegration

logger = logging.getLogger("p1_serial")

DEFAULT_PORT = "/dev/ttyUSB0"
DEFAULT_BAUDRATE = 115200
TELEGRAM_TIMEOUT = 15  # seconden — DSMR-meters sturen elke ~1s een telegram

# Een DSMR-regel ziet er generiek uit als: <obis-code>(<waarde1>)(<waarde2>)...
# bv. "1-0:1.8.1(001234.567*kWh)" of "0-1:24.2.1(230101120000W)(01234.567*m3)".
OBIS_LINE = re.compile(r"^(\d+-\d+:\d+\.\d+\.\d+(?:\.\d+)?)((?:\([^)]*\))+)", re.MULTILINE)
OBIS_VALUES = re.compile(r"\(([^)]*)\)")


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
    """Parseer een compleet DSMR-telegram naar een ruwe {obis_code: waarde(n)} dict.
    Een OBIS-regel met 1 waarde levert een string op, met meerdere waarden een lijst
    (bv. gas: tijdstempel + m3-stand). Gooit ValueError bij een ongeldige CRC of een
    telegram zonder herkenbare OBIS-regels."""
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

    obis_data: dict[str, str | list[str]] = {}
    for m in OBIS_LINE.finditer(text):
        code = m.group(1)
        values = OBIS_VALUES.findall(m.group(2))
        obis_data[code] = values[0] if len(values) == 1 else values

    if not obis_data:
        raise ValueError("Geen OBIS-velden gevonden in telegram")

    return obis_data


def _power_port(ser):
    """Zet RTS (en voor de zekerheid DTR) expliciet hoog na het openen.

    Een rechtstreekse P1-kabel in de meter werkt ook zonder dit, maar een actieve
    HomeWizard P1-splitter haalt zijn eigen voeding uit de RTS-lijn van de USB-kabel
    (hij versterkt/splitst het signaal en heeft daar stroom voor nodig) — zonder
    expliciet hoog te zetten laat pyserial dit standaard aan de driver over, wat
    kennelijk niet genoeg is om zo'n splitter van stroom te voorzien."""
    try:
        ser.rts = True
        ser.dtr = True
    except Exception as e:
        logger.debug(f"Kon RTS/DTR niet zetten (mogelijk niet ondersteund door deze poort): {e}")


def _lock_port(ser, port: str):
    """Legt een exclusieve, non-blocking flock op de seriële poort-fd, zodat de
    worker-pollloop en een losse integratietest (aparte processen: mtd-worker
    resp. mtd-core) elkaar niet in de weg zitten. Zonder deze lock lezen beide
    kanten soms bytes van elkaars telegram weg, wat zich uit als CRC-fouten of
    als een 'geen data' time-out — zie beide foutmeldingen in de UI. De lock
    wordt automatisch vrijgegeven zodra de poort gesloten wordt."""
    try:
        fcntl.flock(ser.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        ser.close()
        raise RuntimeError(
            f"Poort {port} is al in gebruik door een lopende meting, probeer over enkele seconden opnieuw"
        )


def _read_telegram(ser) -> bytes:
    """Lees van de seriële poort tot een compleet telegram ('/' ... '!CRC') binnen is.

    ser.readline() timet alleen individueel uit (geen bytes binnen TELEGRAM_TIMEOUT):
    bij een ruisende/instabiele verbinding die voortdurend halve of foutieve regels
    aflevert (nooit een langere stilte dan de timeout) zou deze lus zonder een eigen
    totale deadline voor altijd door kunnen lezen zonder ooit een compleet telegram
    te vinden — en daarmee de hele (single-threaded) worker-lus blokkeren."""
    deadline = time.monotonic() + TELEGRAM_TIMEOUT
    buf = bytearray()
    collecting = False
    while True:
        if time.monotonic() >= deadline:
            raise TimeoutError("Geen compleet telegram ontvangen van P1-poort binnen de time-out")

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
        ser = serial.Serial(
            port=port,
            baudrate=baudrate,
            bytesize=serial.EIGHTBITS if baudrate == 115200 else serial.SEVENBITS,
            parity=serial.PARITY_NONE if baudrate == 115200 else serial.PARITY_EVEN,
            stopbits=serial.STOPBITS_ONE,
            timeout=TELEGRAM_TIMEOUT,
            xonxoff=False,
            rtscts=False,
        )
        _power_port(ser)
        _lock_port(ser, port)
        return ser

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

        _power_port(ser)
        _lock_port(ser, port)

        try:
            raw = _read_telegram(ser)
            data = parse_telegram(raw)
        except (TimeoutError, ValueError) as e:
            raise RuntimeError(str(e))
        finally:
            ser.close()

        # Ruwe OBIS-dump, puur ter bevestiging dat er echt (en geldig) telegram-
        # verkeer binnenkomt — geen interpretatie hier, dat gebeurt platformkant.
        return {"port": port, "baudrate": baudrate, **{
            k: (", ".join(v) if isinstance(v, list) else v) for k, v in data.items()
        }}

    def close(self):
        if self._serial is not None:
            try:
                self._serial.close()
            except Exception:
                pass
            self._serial = None

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
            logger.debug(f"P1 serieel: {len(data)} OBIS-velden gelezen")
        except Exception as e:
            logger.warning(f"P1 serieel fout: {e}")
            self.report_error(str(e))
            if self._serial is not None:
                try:
                    self._serial.close()
                except Exception:
                    pass
                self._serial = None
