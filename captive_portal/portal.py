import json
import logging
import os
import subprocess
from flask import Flask, render_template, request, jsonify

logger = logging.getLogger("portal")
app = Flask(__name__)

CONFIG_PATH = os.environ.get("MTD_CONFIG", "/opt/mtd-agent/config.json")


def save_config(data: dict):
    os.makedirs(os.path.dirname(CONFIG_PATH), exist_ok=True)
    existing = {}
    try:
        with open(CONFIG_PATH) as f:
            existing = json.load(f)
    except Exception:
        pass
    existing.update(data)
    with open(CONFIG_PATH, "w") as f:
        json.dump(existing, f, indent=2)


def connect_wifi(ssid: str, password: str):
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


@app.route("/")
def index():
    return render_template("index.html")


@app.route("/api/setup", methods=["POST"])
def setup():
    data = request.json
    api_key = data.get("api_key", "").strip()
    instance_key = data.get("instance_key", "").strip()

    if not api_key.startswith("ea_"):
        return jsonify({"error": "API key moet beginnen met ea_"}), 400
    if not instance_key:
        return jsonify({"error": "Instance key is verplicht"}), 400

    save_config({
        "api_key": api_key,
        "instance_key": instance_key
    })

    ssid = data.get("ssid")
    password = data.get("wifi_password", "")
    if ssid:
        connect_wifi(ssid, password)

    subprocess.Popen(["bash", "-c",
        "sleep 2 && systemctl enable mtd-core mtd-worker "
        "&& systemctl restart mtd-core mtd-worker "
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
