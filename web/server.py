import subprocess
import time
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

import config
from core import database
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

    @app.get("/api/device")
    def api_device():
        return {
            "device_id": agent.device_id or _pi_serial(),
            "agent_version": get_agent_version(),
            "uptime_s": int(time.monotonic() - _START_TIME),
            "network_mode": database.get_device_config("network_mode", "lan"),
            "agent_key": config.AGENT_KEY,
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

    return app
