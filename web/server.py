import ipaddress
import re
import shutil
import socket
import subprocess
import time
from datetime import datetime, timezone
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

import config
from core import database
from core.agent import _get_logs
from core.env_file import write_agent_key
from core.version import get_agent_version

STATIC_DIR = Path(__file__).parent / "static"
_START_TIME = time.monotonic()
_pi_serial_cache: str | None = None


def _pi_serial() -> str | None:
    """Uniek hardware-serienummer van de Pi zelf (uit /proc/cpuinfo) — anders
    dan agent.device_id (het platform-ID) blijft dit altijd beschikbaar, ook
    vóórdat een device aan het platform gekoppeld is."""
    global _pi_serial_cache
    if _pi_serial_cache is not None:
        return _pi_serial_cache
    try:
        with open("/proc/cpuinfo") as f:
            for line in f:
                if line.startswith("Serial"):
                    _pi_serial_cache = line.split(":", 1)[1].strip()
                    return _pi_serial_cache
    except OSError:
        pass
    return None


def _local_ip() -> str | None:
    """Lokaal LAN-IP zonder daadwerkelijk iets te versturen — de socket wordt
    nooit verbonden, dit laat het OS alleen de uitgaande interface/route voor
    8.8.8.8 kiezen zodat we het bijbehorende lokale adres kunnen aflezen."""
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.connect(("8.8.8.8", 80))
            return s.getsockname()[0]
    except OSError:
        return None


def _subnet_mask(ip: str | None) -> str | None:
    """Subnetmasker van de interface die `ip` heeft, via `ip -o -4 addr show`
    (standaard aanwezig op Raspberry Pi OS)."""
    if not ip:
        return None
    try:
        result = subprocess.run(
            ["ip", "-o", "-4", "addr", "show"],
            capture_output=True, text=True, timeout=3,
        )
        for line in result.stdout.splitlines():
            match = re.search(r"inet (\d+\.\d+\.\d+\.\d+)/(\d+)", line)
            if match and match.group(1) == ip:
                return str(ipaddress.IPv4Network(f"0.0.0.0/{match.group(2)}").netmask)
    except (OSError, subprocess.SubprocessError):
        pass
    return None


def _disk_usage() -> dict:
    """Schijfruimte van de SD-kaart — SD-kaarten zijn de zwakke plek bij een
    24/7 draaiende Pi, dus vroeg zicht op vollopen is hier meer waard dan
    ergens anders."""
    try:
        usage = shutil.disk_usage("/")
        return {
            "disk_total_gb": round(usage.total / 1_000_000_000, 1),
            "disk_used_gb": round(usage.used / 1_000_000_000, 1),
            "disk_percent": round(usage.used / usage.total * 100, 1),
        }
    except OSError:
        return {"disk_total_gb": None, "disk_used_gb": None, "disk_percent": None}


def _cpu_temp_c() -> float | None:
    try:
        with open("/sys/class/thermal/thermal_zone0/temp") as f:
            return round(int(f.read().strip()) / 1000, 1)
    except (OSError, ValueError):
        return None


def _boot_time() -> str | None:
    try:
        with open("/proc/uptime") as f:
            uptime_s = float(f.read().split()[0])
        boot_ts = datetime.now(timezone.utc).timestamp() - uptime_s
        return datetime.fromtimestamp(boot_ts, tz=timezone.utc).isoformat()
    except (OSError, ValueError, IndexError):
        return None


class TokenRequest(BaseModel):
    token: str


def build_app(agent) -> FastAPI:
    app = FastAPI(title="MTD Agent")
    app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")

    @app.get("/")
    def index():
        return FileResponse(STATIC_DIR / "index.html")

    @app.get("/api/health")
    def api_health():
        meta = database.plugin_metadata()
        plugins = [
            {
                **p,
                "label": meta.get(p["id"], {}).get("label"),
                "slug": meta.get(p["id"], {}).get("slug"),
                "integration_name": meta.get(p["id"], {}).get("integration_name"),
                "installed_version": meta.get(p["id"], {}).get("installed_version"),
            }
            for p in agent.health.snapshot()
        ]
        sync = agent.sync
        if sync and sync.authenticated:
            agent_status = "online"
        elif sync and sync.auth_error:
            agent_status = "auth_error"
        else:
            agent_status = "offline"
        return {
            "agent_status": agent_status,
            "auth_error": sync.auth_error if sync else None,
            "plugins": plugins,
        }

    @app.get("/api/readings")
    def api_readings():
        # Alleen nog niet-gesynchroniseerde readings: acceptabele MVP-aanname
        # omdat de flush-interval kort is (30s); een "laatste per plugin"-tabel
        # is een logische vervolgstap als dit een blinde vlek blijkt.
        latest_by_plugin: dict[str, dict] = {}
        for row in database.unsynced_readings(limit=1000):
            source = row["source"]
            existing = latest_by_plugin.get(source)
            if not existing or row["timestamp"] > existing["timestamp"]:
                latest_by_plugin[source] = dict(row)
        return latest_by_plugin

    @app.get("/api/readings/{source}")
    def api_readings_latest(source: str):
        rows = database.latest_readings_batch(source)
        return {
            "timestamp": rows[0]["timestamp"] if rows else None,
            "readings": [
                {"metric": r["metric"], "value": r["value"], "unit": r["unit"], "direction": r["direction"]}
                for r in rows
            ],
        }

    @app.get("/api/device")
    def api_device():
        local_ip = _local_ip()
        return {
            "device_id": agent.device_id or _pi_serial(),
            "agent_version": get_agent_version(),
            "uptime_s": int(time.monotonic() - _START_TIME),
            "network_mode": database.get_device_config("network_mode", "lan"),
            "agent_key": config.AGENT_KEY,
            "local_ip": local_ip,
            "subnet_mask": _subnet_mask(local_ip),
            "boot_time": _boot_time(),
            "cpu_temp_c": _cpu_temp_c(),
            **_disk_usage(),
        }

    @app.post("/api/token")
    def api_token(body: TokenRequest):
        """Werk het agent-token (AGENT_KEY) bij — voor als je het token op het
        platform op de ouderwetse manier hebt gegenereerd en hier wilt
        koppelen, of na een tokenrotatie. Herstart de agent-service zodat de
        nieuwe waarde meteen gebruikt wordt."""
        token = body.token.strip()
        if not token.startswith("mtd_agent_"):
            raise HTTPException(status_code=422, detail="Token moet beginnen met 'mtd_agent_'")
        write_agent_key(token)
        subprocess.Popen(["bash", "-c", "sleep 1 && systemctl restart mtd-agent"])
        return {"ok": True, "message": "Token opgeslagen, agent herstart..."}

    @app.post("/api/restart")
    def api_restart():
        subprocess.Popen(["bash", "-c", "sleep 1 && systemctl restart mtd-agent"])
        return {"ok": True, "message": "Agent herstart..."}

    @app.get("/api/logs")
    async def api_logs(lines: int = 200):
        result = await _get_logs({"lines": lines})
        if not result.get("success"):
            raise HTTPException(status_code=500, detail=result.get("error") or "Logs ophalen mislukt")
        return result

    return app
