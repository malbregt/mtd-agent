#!/bin/bash
# Eén-commando installatie: geen onboarding-flow, geen aparte portal-service.
# Alle argumenten zijn optioneel — zonder argumenten installeert dit script
# gewoon een kale, ongekoppelde agent; token en plugin(s) koppel je daarna via
# de lokale statuspagina (http://mtd-bridge.local:8080) resp. het platform
# (config-push naar het device), niet via dit script.
#
# Kale installatie (op de Pi, als root/via sudo):
#   curl -fsSL https://raw.githubusercontent.com/malbregt/mtd-agent/v2-async-rebuild/install.sh \
#     | sudo bash
#
# Optioneel vooraf al meegeven (bv. voor scripted rollouts): --agent-key,
# --plugin/--plugin-config, --device-id (informatief, het platform herleidt
# het device zelf uit --agent-key), --hostname (default: mtd-bridge-<4hex van
# CPU-serienummer>, al uniek per bridge — override alleen als je een eigen
# naam wilt):
#   curl -fsSL https://raw.githubusercontent.com/malbregt/mtd-agent/v2-async-rebuild/install.sh \
#     | sudo bash -s -- --agent-key mtd_agent_xxxxxxxx
#
# Of lokaal na een git clone: bash install.sh [--agent-key ...] [--plugin ... --plugin-config ...]
set -euo pipefail

REPO_URL="https://github.com/malbregt/mtd-agent.git"
BRANCH="v2-async-rebuild"
INSTALL_DIR="/opt/mtd-agent"
DEVICE_ID=""
AGENT_KEY=""
PLUGIN_ID=""
PLUGIN_CONFIG="{}"
# Standaard hostname, bereikbaar via mDNS (avahi). Wordt hieronder automatisch
# uniek gemaakt met een stukje CPU-serienummer (mtd-bridge-<4hex>), zodat
# meerdere bridges op hetzelfde netwerk niet botsen op dezelfde .local-naam.
# Met --hostname override je dit volledig naar een eigen naam.
HOSTNAME_VALUE=""

while [ $# -gt 0 ]; do
  case "$1" in
    --repo) REPO_URL="$2"; shift 2 ;;
    --branch) BRANCH="$2"; shift 2 ;;
    --device-id) DEVICE_ID="$2"; shift 2 ;;
    --agent-key) AGENT_KEY="$2"; shift 2 ;;
    --plugin) PLUGIN_ID="$2"; shift 2 ;;
    --plugin-config) PLUGIN_CONFIG="$2"; shift 2 ;;
    --hostname) HOSTNAME_VALUE="$2"; shift 2 ;;
    *) echo "Onbekende optie: $1"; exit 1 ;;
  esac
done

if [ -z "$HOSTNAME_VALUE" ]; then
  # Laatste 4 hex-tekens van het CPU-serienummer (/proc/cpuinfo, Raspberry Pi
  # specifiek) als suffix, zodat meerdere bridges out-of-the-box een unieke
  # .local-naam krijgen zonder dat je --hostname hoeft mee te geven.
  SERIAL_SUFFIX="$(awk -F': ' '/^Serial/ {print $2}' /proc/cpuinfo 2>/dev/null | tail -c 5)"
  if [ -n "$SERIAL_SUFFIX" ]; then
    HOSTNAME_VALUE="mtd-bridge-$SERIAL_SUFFIX"
  else
    HOSTNAME_VALUE="mtd-bridge"
  fi
fi

echo "=== MTD Agent — automatische installatie ==="

echo "[1/7] Systeempakketten..."
apt-get update -qq
apt-get install -y -qq git python3 python3-venv python3-pip avahi-daemon

echo "[2/7] Hostname instellen ($HOSTNAME_VALUE.local)..."
if [ "$(hostname)" != "$HOSTNAME_VALUE" ]; then
  hostnamectl set-hostname "$HOSTNAME_VALUE"
  sed -i "s/127\.0\.1\.1.*/127.0.1.1\t$HOSTNAME_VALUE/" /etc/hosts
  if ! grep -q "127\.0\.1\.1" /etc/hosts; then
    echo -e "127.0.1.1\t$HOSTNAME_VALUE" >> /etc/hosts
  fi
  systemctl restart avahi-daemon 2>/dev/null || true
fi

echo "[3/7] Repo ophalen (branch: $BRANCH)..."
if [ -d "$INSTALL_DIR/.git" ]; then
  git -C "$INSTALL_DIR" fetch origin "$BRANCH"
  git -C "$INSTALL_DIR" checkout "$BRANCH"
  git -C "$INSTALL_DIR" reset --hard "origin/$BRANCH"
else
  git clone --branch "$BRANCH" "$REPO_URL" "$INSTALL_DIR"
fi

mkdir -p /data /etc/mtd-agent

echo "[4/7] Agent-token wegschrijven..."
cat > /etc/mtd-agent/env <<EOF
AGENT_KEY=$AGENT_KEY
DB_PATH=/data/agent.db
PLUGIN_DIR=/data/plugins
EOF
chmod 600 /etc/mtd-agent/env

echo "[5/7] Python-omgeving aanmaken (venv, deps)..."
python3 -m venv "$INSTALL_DIR/venv"
"$INSTALL_DIR/venv/bin/pip" install -q -r "$INSTALL_DIR/requirements.txt"

echo "[6/7] Systemd-unit installeren, device-config en plugin registreren..."
cp "$INSTALL_DIR/systemd/mtd-agent.service" /etc/systemd/system/
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

device_id = os.environ.get("MTD_DEVICE_ID", "")
plugin_id = os.environ["MTD_PLUGIN_ID"]
plugin_config_raw = os.environ.get("MTD_PLUGIN_CONFIG", "{}").strip()

database.init_db("/data/agent.db")
if device_id:
    database.set_device_config("device_id", device_id)
database.set_device_config("onboarded", "true")
database.set_device_config("network_mode", "lan")

if plugin_id:
    plugin_config = json.loads(plugin_config_raw or "{}")
    database.upsert_plugin(plugin_id, installed_version="2.0.0",
                            config_json=json.dumps(plugin_config), status="installed")

print("device_config + agent_plugins ingevuld")
PYEOF

echo "[7/7] Agent starten..."
systemctl enable mtd-agent
systemctl restart mtd-agent

echo ""
echo "Klaar. Status:  sudo systemctl status mtd-agent"
echo "        Logs:   sudo journalctl -u mtd-agent -f"
echo "        Lokale statuspagina: http://$HOSTNAME_VALUE.local:8080 (of http://$(hostname -I | awk '{print $1}'):8080)"
if [ -z "$AGENT_KEY" ]; then
  echo ""
  echo "Nog geen agent-token meegegeven — vul 'm in op de lokale statuspagina hierboven."
fi
