import asyncio
import inspect
import json
import logging
import os
import shutil
import subprocess
import time
from pathlib import Path

import aiohttp

import config
from core import database
from core.bus import Bus
from core.health import HealthTracker
from core.plugin import Reading
from core.version import get_agent_version

log = logging.getLogger("sync")

INSTALL_DIR = Path(__file__).resolve().parent.parent


class SyncClient:
    """Persistente WebSocket-verbinding met het platform. Vier channels:
    config (platform->agent), data (agent->platform), health (agent->platform),
    command (platform->agent) + ack (agent->platform). Platform offline bij
    opstart/tijdens gebruik is geen probleem — readings/health blijven lokaal
    in SQLite staan tot de volgende geslaagde flush."""

    def __init__(self, bus: Bus, health: HealthTracker, device_id: str, on_config=None, on_command=None):
        self.bus = bus
        self.health = health
        self.device_id = device_id
        self.on_config = on_config
        self.on_command = on_command
        self._ws: aiohttp.ClientWebSocketResponse | None = None
        self._started_at = time.monotonic()
        self._reading_queue: asyncio.Queue[Reading] = bus.subscribe("reading")
        # Aparte flag naast self._ws: de WS-handshake (TCP+HTTP-upgrade) kan
        # slagen terwijl het platform het token daarna alsnog afwijst — pas de
        # "connected"-bevestiging van het platform betekent echt geauthenticeerd.
        self.authenticated = False
        self.auth_error: str | None = None

    async def run(self) -> None:
        await asyncio.gather(
            self._connection_loop(),
            self._readings_flush_loop(),
            self._health_flush_loop(),
            self._reading_intake_loop(),
            self._config_refresh_loop(),
        )

    async def _connection_loop(self) -> None:
        while True:
            try:
                async with aiohttp.ClientSession() as session:
                    headers = {"X-Api-Key": config.AGENT_KEY}
                    async with session.ws_connect(
                        f"{config.PLATFORM_WS_URL}?token={config.AGENT_KEY}", headers=headers
                    ) as ws:
                        self._ws = ws
                        log.debug("WS-handshake gelukt, wacht op authenticatie-bevestiging")
                        async for msg in ws:
                            if msg.type == aiohttp.WSMsgType.TEXT:
                                await self._handle_message(json.loads(msg.data))
                            elif msg.type in (aiohttp.WSMsgType.ERROR, aiohttp.WSMsgType.CLOSED):
                                break
                        if not self.authenticated and not self.auth_error:
                            # Verbinding verbroken vóórdat "connected" of "error" ooit
                            # binnenkwam — geen duidelijke reden, wel vermelden.
                            self.auth_error = "Verbinding verbroken vóór authenticatie-bevestiging"
            except Exception as e:
                log.warning("WS-verbinding mislukt/verbroken: %s — herverbinden over %ss", e, config.WS_RECONNECT_DELAY_S)
            self._ws = None
            self.authenticated = False
            await asyncio.sleep(config.WS_RECONNECT_DELAY_S)

    async def _handle_message(self, msg: dict) -> None:
        msg_type = msg.get("type")
        if msg_type == "connected":
            self.authenticated = True
            self.auth_error = None
            log.info("verbonden en geauthenticeerd bij platform (device_id=%s)", msg.get("device_id"))
            return
        if msg_type == "error":
            self.auth_error = msg.get("detail") or "onbekende fout"
            self.authenticated = False
            log.error("authenticatie bij platform mislukt: %s — controleer AGENT_KEY", self.auth_error)
            return

        channel = msg.get("channel")
        if channel == "config" and self.on_config:
            plugins = msg.get("plugins", msg.get("integrations", []))
            log.info("config ontvangen: %d plugin(s) — %s", len(plugins),
                     ", ".join(p.get("plugin_id") or p.get("integration_id", "?") for p in plugins))
            await self.on_config(msg)
        elif channel == "command" and self.on_command:
            command_id = msg.get("id") or msg.get("request_id")
            log.info("command ontvangen: id=%s type=%s plugin=%s", command_id, msg.get("type"), msg.get("plugin_id"))
            result = await self.on_command(msg)
            ack: dict = {"channel": "ack", "command_id": command_id}
            if isinstance(result, dict):
                ack["result"] = result
            else:
                ack["status"] = result or "received"
            await self._send(ack)
        elif msg.get("type") == "test_integration":
            # Legacy commandotype (geen "channel"-veld, zelfde als v1.0.26):
            # platform vraagt om een eenmalige verbindingstest met een nog niet
            # opgeslagen config, gebruikt door de "Test verbinding"-knop in de UI.
            log.info("test_integration ontvangen voor %s", msg.get("integration_id"))
            await self._handle_test_integration(msg)
        elif msg.get("type") == "config_update":
            # Legacy signaal (geen "channel"-veld, zelfde als v1.0.26): platform
            # stuurt dit bij elke wijziging aan een sub-integratie (toevoegen,
            # config aanpassen, pauzeren), maar zonder de pluginlijst zelf mee te
            # sturen — de agent moet die apart ophalen via GET /agent/config.
            log.info("config_update-signaal ontvangen, config opnieuw ophalen")
            await self._refetch_config()
        elif msg.get("type") == "update":
            # Legacy commandotype (geen "channel"-veld, zelfde als v1.0.26):
            # platform vraagt om een OTA-update van de agent-kern zelf naar de
            # opgegeven git-tag/versie (los van plugin-versies, zie
            # core/plugin_download.py voor die kant).
            version = msg.get("version")
            log.warning("update-commando ontvangen: agent wordt bijgewerkt naar %s", version)
            self._trigger_update(version)

    def _trigger_update(self, version: str) -> None:
        """Kopieert het update-script naar /tmp en voert het als losstaand
        proces uit — het script doet zelf een `git checkout` in de working
        tree waar dit script ORIGINEEL vandaan komt; als het in-place vanuit
        die working tree bleef draaien, zou bash halverwege een mix van oude/
        nieuwe scriptinhoud kunnen uitvoeren (bash leest scripts gebufferd
        van schijf). Vanuit /tmp draaiend is het script zelf niet meer
        onderdeel van wat git overschrijft."""
        src = INSTALL_DIR / "scripts" / "update.sh"
        tmp = Path("/tmp/mtd-agent-update.sh")
        try:
            shutil.copy(src, tmp)
            os.chmod(tmp, 0o755)
            subprocess.Popen(["bash", str(tmp), version], env=os.environ.copy())
        except Exception as e:
            log.error("kon update-script niet starten: %s", e)

    async def _refetch_config(self) -> None:
        if not self.on_config:
            return
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(
                    f"{config.PLATFORM_API_URL}/agent/config",
                    headers={"X-Api-Key": config.AGENT_KEY},
                    timeout=aiohttp.ClientTimeout(total=10),
                ) as resp:
                    resp.raise_for_status()
                    payload = await resp.json()
        except Exception as e:
            log.warning("config ophalen mislukt: %s", e)
            return

        plugins = payload.get("plugins", payload.get("integrations", []))
        log.info("config ontvangen (via ophalen): %d plugin(s) — %s", len(plugins),
                 ", ".join(p.get("plugin_id") or p.get("integration_id", "?") for p in plugins) or "geen")
        await self.on_config(payload)

    async def _config_refresh_loop(self) -> None:
        """Vangnet naast de event-driven config_update-pushes: haalt periodiek
        gewoon opnieuw de volledige config op, voor het geval een push-signaal
        om wat voor reden dan ook nooit aankomt."""
        while True:
            await asyncio.sleep(config.CONFIG_REFRESH_INTERVAL_S)
            log.info("periodieke config-refresh")
            await self._refetch_config()

    async def _handle_test_integration(self, msg: dict) -> None:
        from core.agent import _load_plugin_class  # lazy: voorkomt circulaire import met core.agent

        request_id = msg.get("request_id")
        integration_id = msg.get("integration_id", "")
        test_config = msg.get("config") or {}
        target_version = msg.get("target_version")
        start = time.monotonic()
        try:
            # Nieuwe integratie: de plugin staat mogelijk nog niet lokaal (niet
            # vendored, nog nooit gedownload). Download hem gericht op basis van
            # de versie/checksum die de backend meestuurt, i.p.v. te wachten op
            # de volgende config-sync (zie _on_config_push voor dezelfde flow).
            if target_version and database.get_installed_version(integration_id) != target_version:
                from core.plugin_download import ensure_plugin_version
                if await ensure_plugin_version(integration_id, target_version, msg.get("target_sha256")):
                    database.upsert_plugin(integration_id, installed_version=target_version)
                else:
                    log.warning("plugin %s: download van versie %s mislukt vóór test", integration_id, target_version)

            plugin_cls = _load_plugin_class(integration_id)
            test_fn = plugin_cls.test_connection
            if inspect.iscoroutinefunction(test_fn):
                device_info = await test_fn(test_config)
            else:
                loop = asyncio.get_running_loop()
                device_info = await loop.run_in_executor(None, test_fn, test_config)
            await self._send({
                "type": "test_result", "request_id": request_id, "success": True,
                "response_ms": int((time.monotonic() - start) * 1000), "device": device_info,
            })
        except Exception as e:
            log.warning("test_integration voor %s mislukt: %s", integration_id, e)
            await self._send({
                "type": "test_result", "request_id": request_id, "success": False,
                "response_ms": int((time.monotonic() - start) * 1000), "error": str(e),
            })

    async def _send(self, payload: dict) -> bool:
        if not self._ws:
            return False
        try:
            await self._ws.send_json(payload)
            return True
        except Exception as e:
            log.warning("versturen mislukt: %s", e)
            return False

    async def _reading_intake_loop(self) -> None:
        """Slaat readings uit de bus meteen lokaal op in SQLite (durable), de
        flush-loop stuurt ze vervolgens periodiek naar het platform."""
        while True:
            event = await self._reading_queue.get()
            r: Reading = event.payload
            database.store_reading(r.device_id, r.metric, r.value, r.unit, r.direction, r.source, r.timestamp.isoformat())

    async def _readings_flush_loop(self) -> None:
        while True:
            await asyncio.sleep(config.READINGS_FLUSH_INTERVAL_S)
            rows = database.unsynced_readings()
            if not rows:
                continue
            # Groepeer per (source, timestamp): metingen die in dezelfde collect()-
            # cyclus zijn opgehaald horen bij elkaar (bv. alle OBIS-velden van één
            # P1-telegram) en moeten als één item bij de platform-normalisatie
            # aankomen, niet los per metric.
            grouped: dict[tuple[str, str], dict] = {}
            ids_by_group: dict[tuple[str, str], list[int]] = {}
            for r in rows:
                key = (r["source"], r["timestamp"])
                grouped.setdefault(key, {})[r["metric"]] = {"value": r["value"], "unit": r["unit"]}
                ids_by_group.setdefault(key, []).append(r["id"])

            readings = [
                {"integration_id": source, "timestamp": ts, "data": data}
                for (source, ts), data in grouped.items()
            ]
            if await self._send({"channel": "data", "readings": readings}):
                database.mark_synced([i for ids in ids_by_group.values() for i in ids])
                log.info("readings verstuurd: %d item(s), %d meting(en)", len(readings), len(rows))
            else:
                log.warning("readings NIET verstuurd (geen WS-verbinding) — blijven lokaal gebufferd (%d meting(en))", len(rows))

    async def _health_flush_loop(self) -> None:
        while True:
            plugins = self.health.snapshot()
            sent = await self._send({
                "channel": "health",
                "agent_version": get_agent_version(),
                "uptime_s": int(time.monotonic() - self._started_at),
                "plugins": plugins,
            })
            if sent:
                log.info("health verstuurd: %d plugin(s) — %s", len(plugins),
                         ", ".join(f"{p['id']}={p['status']}" for p in plugins) or "geen")
            else:
                log.warning("health NIET verstuurd (geen WS-verbinding)")
            await asyncio.sleep(config.HEALTH_FLUSH_INTERVAL_S)
