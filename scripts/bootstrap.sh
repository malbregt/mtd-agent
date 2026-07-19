#!/bin/bash
# Eén commando voor het "kale" gedeelte van de installatie: repo ophalen +
# provisioning. Vult bewust géén device-token in — dat gebeurt via de
# normale captive-portal-onboarding (hotspot "MTD-Setup" -> 192.168.4.1)
# die provision.sh automatisch start zolang er nog geen /data/agent.db is.
#
# Gebruik (op de Pi, als root/via sudo):
#   curl -fsSL https://raw.githubusercontent.com/malbregt/mtd-agent/v2-async-rebuild/scripts/bootstrap.sh | sudo bash
set -euo pipefail

REPO_URL="https://github.com/malbregt/mtd-agent.git"
BRANCH="v2-async-rebuild"
INSTALL_DIR="/opt/mtd-agent"

echo "=== MTD Agent — bootstrap ==="

echo "[1/3] Systeempakketten..."
apt-get update -qq
apt-get install -y -qq git python3 python3-venv python3-pip

echo "[2/3] Repo ophalen (branch: $BRANCH)..."
if [ -d "$INSTALL_DIR/.git" ]; then
  git -C "$INSTALL_DIR" fetch origin "$BRANCH"
  git -C "$INSTALL_DIR" checkout "$BRANCH"
  git -C "$INSTALL_DIR" reset --hard "origin/$BRANCH"
else
  git clone --branch "$BRANCH" "$REPO_URL" "$INSTALL_DIR"
fi

echo "[3/3] Provisioning (venv, services, hotspot indien nodig)..."
bash "$INSTALL_DIR/scripts/provision.sh"

echo ""
echo "Klaar. Verbind nu met wifi-netwerk 'MTD-Setup' en ga naar http://192.168.4.1"
echo "om het apparaat te koppelen (instance key invullen)."
