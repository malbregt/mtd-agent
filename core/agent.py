import asyncio
import importlib.util
import json
import logging
import socket
import sys
from pathlib import Path

import config
from core import database
from core.bus import Bus
from core.health import HealthTracker
from core.plugin import DevicePlugin
from core.supervisor import Supervisor
from core.sync import SyncClient

log = logging.getLogger("agent")


def _lan_available(host: str = "8.8.8.8", port: int = 53, timeout: float = 2.0) -> bool:
    try:
        socket.create_connection((host, port), timeout=timeout).close()
        return True
    except OSError:
        return False


def _load_plugin_class(plugin_id: str) -> type[DevicePlugin]:
    """Laadt plugins/{plugin_id}/plugin.py dynamisch via importlib en zoekt de
    eerste DevicePlugin-subklasse erin — geen registratie/decorator nodig."""
    plugin_dir = Path(config.PLUGIN_DIR) / plugin_id
    module_path = plugin_dir / "plugin.py"
    spec = importlib.util.spec_from_file_location(f"plugins.{plugin_id}", module_path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)

    for attr in vars(module).values():
        if isinstance(attr, type) and issubclass(attr, DevicePlugin) and attr is not DevicePlugin:
            return attr
    raise RuntimeError(f"Geen DevicePlugin-subklasse gevonden in {module_path}")


class Agent:
    def __init__(self):
        self.bus = Bus()
        self.health = HealthTracker()
        self.supervisor = Supervisor(self.bus, self.health)
        self.device_id = None
        self.sync: SyncClient | None = None

    async def bootstrap(self) -> None:
        database.init_db()
        self.device_id = database.get_device_config("device_id")

        if not _lan_available():
            log.warning("geen LAN beschikbaar — start hotspot/captive portal")
            from onboarding import portal
            await portal.start_hotspot()

        installed = database.load_installed_plugins()
        for row in installed:
            await self._start_plugin_from_row(row)

        self.sync = SyncClient(
            self.bus, self.health, self.device_id,
            on_config=self._on_config_push, on_command=self._on_command,
        )
        asyncio.create_task(self.sync.run(), name="sync")

    async def _start_plugin_from_row(self, row) -> None:
        plugin_id = row["plugin_id"]
        try:
            plugin_config = json.loads(row["config"] or "{}")
            plugin_cls = _load_plugin_class(plugin_id)
            plugin = plugin_cls(self.device_id, plugin_config)
            collect_interval = plugin_config.get("collect_interval_s", 60)
            self.supervisor.start_plugin(plugin, collect_interval)
        except Exception:
            log.exception("kon plugin %s niet starten", plugin_id)
            database.upsert_plugin(plugin_id, status="failed")

    async def _on_config_push(self, msg: dict) -> None:
        """Platform pusht welke plugins de agent moet hebben (config-channel,
        type=plugin_sync). Vergelijkt met wat lokaal geïnstalleerd is en start/
        herstart wat nodig is. Plugin-download zelf (van GitHub) is nog TODO —
        voor nu wordt alleen config van al-aanwezige plugins bijgewerkt."""
        for plugin in msg.get("plugins", msg.get("integrations", [])):
            plugin_id = plugin.get("plugin_id") or plugin.get("integration_id")
            if not plugin_id:
                continue
            database.upsert_plugin(
                plugin_id,
                target_version=plugin.get("target_version"),
                config_json=json.dumps(plugin.get("config", {})),
            )

    async def _on_command(self, msg: dict) -> str:
        plugin_id = msg.get("plugin_id", "")
        action = msg.get("type", "")
        database.log_command(msg.get("id", ""), plugin_id, action, json.dumps(msg.get("payload", {})), "received")
        return "not_supported"


async def run() -> None:
    logging.basicConfig(level=config.LOG_LEVEL)
    agent = Agent()
    await agent.bootstrap()
    await asyncio.Event().wait()
