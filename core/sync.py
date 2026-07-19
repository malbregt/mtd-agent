import asyncio
import inspect
import json
import logging
import time

import aiohttp

import config
from core import database
from core.bus import Bus
from core.health import HealthTracker
from core.plugin import Reading

log = logging.getLogger("sync")

AGENT_VERSION = "2.0.0"


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

    async def run(self) -> None:
        await asyncio.gather(
            self._connection_loop(),
            self._readings_flush_loop(),
            self._health_flush_loop(),
            self._reading_intake_loop(),
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
                        log.info("verbonden met platform")
                        async for msg in ws:
                            if msg.type == aiohttp.WSMsgType.TEXT:
                                await self._handle_message(json.loads(msg.data))
                            elif msg.type in (aiohttp.WSMsgType.ERROR, aiohttp.WSMsgType.CLOSED):
                                break
            except Exception as e:
                log.warning("WS-verbinding mislukt/verbroken: %s — herverbinden over %ss", e, config.WS_RECONNECT_DELAY_S)
            self._ws = None
            await asyncio.sleep(config.WS_RECONNECT_DELAY_S)

    async def _handle_message(self, msg: dict) -> None:
        channel = msg.get("channel")
        if channel == "config" and self.on_config:
            plugins = msg.get("plugins", msg.get("integrations", []))
            log.info("config ontvangen: %d plugin(s) — %s", len(plugins),
                     ", ".join(p.get("plugin_id") or p.get("integration_id", "?") for p in plugins))
            await self.on_config(msg)
        elif channel == "command" and self.on_command:
            log.info("command ontvangen: id=%s type=%s plugin=%s", msg.get("id"), msg.get("type"), msg.get("plugin_id"))
            result = await self.on_command(msg)
            await self._send({"channel": "ack", "command_id": msg.get("id"), "status": result or "received"})
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

    async def _handle_test_integration(self, msg: dict) -> None:
        from core.agent import _load_plugin_class  # lazy: voorkomt circulaire import met core.agent

        request_id = msg.get("request_id")
        integration_id = msg.get("integration_id", "")
        test_config = msg.get("config") or {}
        start = time.monotonic()
        try:
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
                "agent_version": AGENT_VERSION,
                "uptime_s": int(time.monotonic() - self._started_at),
                "plugins": plugins,
            })
            if sent:
                log.info("health verstuurd: %d plugin(s) — %s", len(plugins),
                         ", ".join(f"{p['id']}={p['status']}" for p in plugins) or "geen")
            else:
                log.warning("health NIET verstuurd (geen WS-verbinding)")
            await asyncio.sleep(config.HEALTH_FLUSH_INTERVAL_S)
