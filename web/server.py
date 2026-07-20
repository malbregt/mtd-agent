import subprocess
import time
from pathlib import Path

import bcrypt
from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

import config
from core import database
from core.env_file import write_agent_key

STATIC_DIR = Path(__file__).parent / "static"
_START_TIME = time.monotonic()


class NetworkRequest(BaseModel):
    password: str
    ssid: str
    wifi_password: str


class PasswordRequest(BaseModel):
    current_password: str
    new_password: str


class ResetPasswordRequest(BaseModel):
    device_id: str


class TokenRequest(BaseModel):
    token: str


def _check_password(password: str) -> bool:
    stored_hash = database.get_device_config("current_password_hash")
    if not stored_hash:
        return False
    return bcrypt.checkpw(password.encode(), stored_hash.encode())


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
            "device_id": agent.device_id,
            "agent_version": "2.0.0",
            "uptime_s": int(time.monotonic() - _START_TIME),
            "network_mode": database.get_device_config("network_mode", "lan"),
            "agent_key": config.AGENT_KEY,
        }

    @app.post("/api/network")
    def api_network(body: NetworkRequest):
        if not _check_password(body.password):
            raise HTTPException(status_code=401, detail="Ongeldig wachtwoord")
        # Netwerkconfiguratie wijzigen (wpa_supplicant) is hardware-specifiek en
        # wordt afgehandeld door onboarding/portal.py — hier alleen doorgeven.
        from onboarding import portal
        portal.apply_wifi_config(body.ssid, body.wifi_password)
        return {"ok": True}

    @app.post("/api/password")
    def api_password(body: PasswordRequest):
        if not _check_password(body.current_password):
            raise HTTPException(status_code=401, detail="Ongeldig wachtwoord")
        new_hash = bcrypt.hashpw(body.new_password.encode(), bcrypt.gensalt()).decode()
        database.set_device_config("current_password_hash", new_hash)
        return {"ok": True}

    @app.post("/api/reset-password")
    def api_reset_password(body: ResetPasswordRequest):
        device_id = database.get_device_config("device_id")
        if body.device_id != device_id:
            raise HTTPException(status_code=401, detail="Ongeldig device_id")
        factory_password = database.get_device_config("factory_password")
        new_hash = bcrypt.hashpw(factory_password.encode(), bcrypt.gensalt()).decode()
        database.set_device_config("current_password_hash", new_hash)
        return {"ok": True}

    @app.post("/api/token")
    def api_token(body: TokenRequest):
        """Werk het agent-token (AGENT_KEY) bij — voor als je het token op het
        platform op de ouderwetse manier hebt gegenereerd en hier wilt
        koppelen, of na een tokenrotatie. Herstart de agent-service zodat de
        nieuwe waarde meteen gebruikt wordt. (Nog geen wachtwoordbescherming —
        komt later.)"""
        token = body.token.strip()
        if not token.startswith("mtd_agent_"):
            raise HTTPException(status_code=422, detail="Token moet beginnen met 'mtd_agent_'")
        write_agent_key(token)
        subprocess.Popen(["bash", "-c", "sleep 1 && systemctl restart mtd-agent"])
        return {"ok": True, "message": "Token opgeslagen, agent herstart..."}

    return app
