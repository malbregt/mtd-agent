#!/bin/bash
set -e

REPO="https://raw.githubusercontent.com/malbregt/mtd-agent/main"
INSTALL_DIR="/opt/mtd-agent"
SERVICE="mtd-agent"

echo "=== MTD Agent Installer ==="

# 1. Systeem updaten
echo "[1/5] Systeem updaten..."
sudo apt-get update -qq
sudo apt-get install -y -qq python3 python3-pip python3-venv git curl

# 2. Agent downloaden
echo "[2/5] Agent downloaden..."
sudo mkdir -p $INSTALL_DIR
sudo git clone https://github.com/malbregt/mtd-agent.git $INSTALL_DIR || \
  (cd $INSTALL_DIR && sudo git pull)

# 3. Python omgeving
echo "[3/5] Python omgeving aanmaken..."
sudo python3 -m venv $INSTALL_DIR/venv
sudo $INSTALL_DIR/venv/bin/pip install -q -r $INSTALL_DIR/requirements.txt

# 4. Config aanmaken indien niet aanwezig
if [ ! -f $INSTALL_DIR/config.json ]; then
  echo "[4/5] Standaard config aanmaken..."
  sudo cp $INSTALL_DIR/config.example.json $INSTALL_DIR/config.json
else
  echo "[4/5] Config al aanwezig, overslaan."
fi

# 5. Systemd service installeren
echo "[5/5] Systemd service installeren..."
sudo cp $INSTALL_DIR/systemd/mtd-agent.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable $SERVICE
sudo systemctl restart $SERVICE

echo ""
echo "✓ MTD Agent geinstalleerd en gestart."
echo "  Status: sudo systemctl status mtd-agent"
echo "  Logs:   sudo journalctl -u mtd-agent -f"
