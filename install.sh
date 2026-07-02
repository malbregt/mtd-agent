#!/bin/bash
set -e

REPO="https://github.com/malbregt/mtd-agent.git"
INSTALL_DIR="/opt/mtd-agent"
SERVICE_AGENT="mtd-agent"
SERVICE_PORTAL="mtd-portal"

echo "=== MTD Agent Installer ==="

# 1. Systeem updaten
echo "[1/6] Systeem updaten..."
apt-get update -qq
apt-get install -y -qq python3 python3-pip python3-venv git curl

# 2. Agent downloaden of updaten
echo "[2/6] Agent downloaden..."
if [ -d "$INSTALL_DIR/.git" ]; then
  cd $INSTALL_DIR && git pull
else
  git clone $REPO $INSTALL_DIR
fi

# 3. Python omgeving
echo "[3/6] Python omgeving aanmaken..."
python3 -m venv $INSTALL_DIR/venv
$INSTALL_DIR/venv/bin/pip install -q -r $INSTALL_DIR/requirements.txt

# 4. Config aanmaken indien niet aanwezig
if [ ! -f $INSTALL_DIR/config.json ]; then
  echo "[4/6] Standaard config aanmaken..."
  cp $INSTALL_DIR/config.example.json $INSTALL_DIR/config.json
  FIRST_INSTALL=true
else
  echo "[4/6] Config al aanwezig, overslaan."
  FIRST_INSTALL=false
fi

# 5. Systemd services installeren
echo "[5/6] Services installeren..."
cp $INSTALL_DIR/systemd/mtd-agent.service /etc/systemd/system/
cp $INSTALL_DIR/systemd/mtd-portal.service /etc/systemd/system/
systemctl daemon-reload

# 6. Starten
echo "[6/6] Starten..."
if [ "$FIRST_INSTALL" = true ]; then
  # Eerste installatie: start captive portal
  echo "Eerste installatie — captive portal starten..."
  bash $INSTALL_DIR/scripts/setup-hotspot.sh
  systemctl enable $SERVICE_PORTAL
  systemctl start $SERVICE_PORTAL
else
  # Update: herstart agent
  systemctl enable $SERVICE_AGENT
  systemctl restart $SERVICE_AGENT
fi

echo ""
echo "✓ MTD Agent geinstalleerd."
if [ "$FIRST_INSTALL" = true ]; then
  echo "  Verbind met WiFi netwerk 'MTD-Setup' om het apparaat te configureren."
else
  echo "  Status: sudo systemctl status mtd-agent"
  echo "  Logs:   sudo journalctl -u mtd-agent -f"
fi
