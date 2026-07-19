#!/bin/bash
# Eén-commando installatie voor een bekend/geprovisioneerd device (device-id en
# agent-token zijn al bekend bij het platform) — slaat de hotspot/captive-portal
# onboarding-flow bewust over, want die is voor onbekende consumentenapparaten.
#
# Gebruik (op de Pi, als root/via sudo):
#   curl -fsSL https://raw.githubusercontent.com/malbregt/mtd-agent/v2-async-rebuild/install.sh \
#     | sudo bash -s -- \
#         --device-id MTD-2026-00123 \
#         --agent-key mtd_agent_xxxxxxxx \
#         --plugin p1_serial \
#         --plugin-config '{"port":"/dev/ttyUSB0","baudrate":115200,"collect_interval_s":10}'
#
# Of lokaal na een git clone: bash install.sh --device-id ... --agent-key ... --plugin ... --plugin-config '...'
set -euo pipefail

REPO_URL="https://github.com/malbregt/mtd-agent.git"
BRANCH="v2-async-rebuild"
INSTALL_DIR="/opt/mtd-agent"
DEVICE_ID=""
AGENT_KEY=""
PLUGIN_ID=""
PLUGIN_CONFIG="{}"

while [ $# -gt 0 ]; do
  case "$1" in
    --repo) REPO_URL="$2"; shift 2 ;;
    --branch) BRANCH="$2"; shift 2 ;;
    --device-id) DEVICE_ID="$2"; shift 2 ;;
    --agent-key) AGENT_KEY="$2"; shift 2 ;;
    --plugin) PLUGIN_ID="$2"; shift 2 ;;
    --plugin-config) PLUGIN_CONFIG="$2"; shift 2 ;;
    *) echo "Onbekende optie: $1"; exit 1 ;;
  esac
done

if [ -z "$DEVICE_ID" ] || [ -z "$AGENT_KEY" ]; then
  echo "Verplicht: --device-id en --agent-key" >&2
  exit 1
fi

echo "=== MTD Agent — automatische installatie ==="

echo "[1/6] Systeempakketten..."
apt-get update -qq
apt-get install -y -qq git python3 python3-venv python3-pip

echo "[2/6] Repo ophalen (branch: $BRANCH)..."
if [ -d "$INSTALL_DIR/.git" ]; then
  git -C "$INSTALL_DIR" fetch origin "$BRANCH"
  git -C "$INSTALL_DIR" checkout "$BRANCH"
  git -C "$INSTALL_DIR" reset --hard "origin/$BRANCH"
else
  git clone --branch "$BRANCH" "$REPO_URL" "$INSTALL_DIR"
fi

mkdir -p /data /etc/mtd-agent

echo "[3/6] Agent-token wegschrijven..."
cat > /etc/mtd-agent/env <<EOF
AGENT_KEY=$AGENT_KEY
DB_PATH=/data/agent.db
PLUGIN_DIR=/data/plugins
EOF
chmod 600 /etc/mtd-agent/env

echo "[4/6] Python-omgeving aanmaken (venv, deps)..."
python3 -m venv "$INSTALL_DIR/venv"
"$INSTALL_DIR/venv/bin/pip" install -q -r "$INSTALL_DIR/requirements.txt"

echo "[5/6] Systemd-units installeren, device-config en plugin registreren..."
cp "$INSTALL_DIR/systemd/mtd-agent.service" "$INSTALL_DIR/systemd/mtd-portal.service" /etc/systemd/system/
systemctl daemon-reload

# Via environment-variabelen doorgeven aan Python i.p.v. shell-string-interpolatie
# in code — voorkomt dat een quote in device-id/plugin-config de heredoc breekt.
export MTD_DEVICE_ID="$DEVICE_ID"
export MTD_PLUGIN_ID="$PLUGIN_ID"
export MTD_PLUGIN_CONFIG="$PLUGIN_CONFIG"

"$INSTALL_DIR/venv/bin/python" <<'PYEOF'
import json, os, sys
sys.path.insert(0, "/opt/mtd-agent")
import config as _cfg
_cfg.DB_PATH = "/data/agent.db"
from core import database

device_id = os.environ["MTD_DEVICE_ID"]
plugin_id = os.environ["MTD_PLUGIN_ID"]
plugin_config_raw = os.environ.get("MTD_PLUGIN_CONFIG", "{}").strip()

database.init_db("/data/agent.db")
database.set_device_config("device_id", device_id)
database.set_device_config("onboarded", "true")
database.set_device_config("network_mode", "lan")

if plugin_id:
    plugin_config = json.loads(plugin_config_raw or "{}")
    database.upsert_plugin(plugin_id, installed_version="2.0.0",
                            config_json=json.dumps(plugin_config), status="installed")

print("device_config + agent_plugins ingevuld")
PYEOF

echo "[6/6] Agent starten..."
systemctl enable mtd-agent
systemctl restart mtd-agent
systemctl disable mtd-portal 2>/dev/null || true
systemctl stop mtd-portal 2>/dev/null || true

echo ""
echo "Klaar. Status:  sudo systemctl status mtd-agent"
echo "        Logs:   sudo journalctl -u mtd-agent -f"
echo "        Lokale statuspagina: http://$(hostname -I | awk '{print $1}'):8080"
