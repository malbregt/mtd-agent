"""
Onboarding / captive portal — logica overgezet uit captive_portal/portal.py
(v1.0.26), nu gekoppeld aan core/database.py (device_config) i.p.v. een los
config.json bestand. Blijft een losstaande Flask-app op poort 80, want dit
draait alleen tijdens de eerste onboarding of na een fabrieksreset — de
hoofd-agent (main.py, poort 8080) draait dan nog niet of is net herstart.
"""
import asyncio
import logging
import subprocess

from flask import Flask, jsonify, render_template, request

from core import database
from core.env_file import write_agent_key

log = logging.getLogger("onboarding")
app = Flask(__name__)

HOTSPOT_SCRIPT = "/opt/mtd-agent/scripts/setup-hotspot.sh"


def apply_wifi_config(ssid: str, password: str) -> None:
    config = f"""country=NL
ctrl_interface=DIR=/var/run/wpa_supplicant GROUP=netdev
update_config=1

network={{
    ssid="{ssid}"
    psk="{password}"
    key_mgmt=WPA-PSK
}}
"""
    with open("/etc/wpa_supplicant/wpa_supplicant.conf", "w") as f:
        f.write(config)
    subprocess.run(["wpa_cli", "-i", "wlan0", "reconfigure"])


async def start_hotspot() -> None:
    """Start de hotspot + captive portal op de achtergrond wanneer er bij
    opstart geen LAN beschikbaar is. Non-blocking t.o.v. de rest van de
    bootstrap-volgorde (agent draait door zodra de gebruiker via de hotspot
    verbinding heeft gelegd en de portal zichzelf uitschakelt)."""
    loop = asyncio.get_running_loop()
    await loop.run_in_executor(None, subprocess.run, ["bash", HOTSPOT_SCRIPT])


@app.route("/")
def index():
    return render_template("index.html")


@app.route("/api/setup", methods=["POST"])
def setup():
    data = request.json
    instance_key = data.get("instance_key", "").strip()
    if not instance_key:
        return jsonify({"error": "Instance key is verplicht"}), 400

    write_agent_key(instance_key)
    database.set_device_config("onboarded", "true")
    database.set_device_config("network_mode", "wifi" if data.get("ssid") else "lan")

    ssid = data.get("ssid")
    password = data.get("wifi_password", "")
    if ssid:
        apply_wifi_config(ssid, password)

    subprocess.Popen(["bash", "-c",
        "sleep 2 && systemctl enable mtd-agent && systemctl restart mtd-agent "
        "&& systemctl disable mtd-portal && systemctl stop mtd-portal"])

    return jsonify({"status": "ok", "message": "Apparaat geconfigureerd, agent start..."})


@app.route("/api/networks", methods=["GET"])
def networks():
    try:
        result = subprocess.run(["iwlist", "wlan0", "scan"], capture_output=True, text=True, timeout=10)
        ssids = []
        for line in result.stdout.splitlines():
            line = line.strip()
            if line.startswith("ESSID:"):
                ssid = line.split('"')[1]
                if ssid and ssid not in ssids:
                    ssids.append(ssid)
        return jsonify({"networks": ssids})
    except Exception as e:
        return jsonify({"networks": [], "error": str(e)})


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=80)
