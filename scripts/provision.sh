#!/bin/bash
set -e

# Installatie/update-logica voor het 1-proces async-model (v2). Geen aparte
# core/worker-services meer, dus geen "welk deel is gewijzigd"-afweging nodig
# zoals in het oude twee-process model: bij elke update herstart simpelweg
# mtd-agent (Restart=always in de unit vangt eventuele crash tijdens herstart op).

INSTALL_DIR="/opt/mtd-agent"
SERVICE_AGENT="mtd-agent"
SERVICE_PORTAL="mtd-portal"

echo "=== MTD Agent Provisioning ==="

echo "[1/3] Python omgeving aanmaken..."
python3 -m venv "$INSTALL_DIR/venv"
"$INSTALL_DIR/venv/bin/pip" install -q -r "$INSTALL_DIR/requirements.txt"

FIRST_INSTALL=false
if [ ! -f /data/agent.db ]; then
  FIRST_INSTALL=true
fi

echo "[2/3] Services installeren..."
cp "$INSTALL_DIR/systemd/mtd-agent.service" /etc/systemd/system/
cp "$INSTALL_DIR/systemd/mtd-portal.service" /etc/systemd/system/
systemctl daemon-reload

echo "[3/3] Starten..."
if [ "$FIRST_INSTALL" = true ]; then
  echo "Eerste installatie — captive portal starten..."
  bash "$INSTALL_DIR/scripts/setup-hotspot.sh"
  systemctl enable "$SERVICE_PORTAL"
  systemctl start "$SERVICE_PORTAL"
else
  systemctl enable "$SERVICE_AGENT"
  systemctl restart "$SERVICE_AGENT"
  systemctl disable "$SERVICE_PORTAL" 2>/dev/null || true
  systemctl stop "$SERVICE_PORTAL" 2>/dev/null || true
fi

echo ""
echo "MTD Agent geinstalleerd."
if [ "$FIRST_INSTALL" = true ]; then
  echo "  Verbind met WiFi netwerk 'MTD-Setup' om het apparaat te configureren."
else
  echo "  Status: sudo systemctl status mtd-agent"
  echo "  Logs:   sudo journalctl -u mtd-agent -f"
fi
