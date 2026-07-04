#!/bin/bash
set -e

# Dit script blijft bewust minimaal en zo stabiel mogelijk: het haalt alleen de
# code op en geeft de rest van de installatie/update-logica door aan
# scripts/provision.sh, dat als NIEUW proces gestart wordt.
#
# Reden: dit bestand overschrijft zichzelf tijdens 'git checkout' (het is
# onderdeel van de repo die net geüpdatet wordt). Bash leest een script echter
# gebufferd van schijf - als je na zo'n checkout gewoon doorloopt in hetzelfde
# script, kan bash een mix van oude en nieuwe inhoud uitvoeren (een klassieke
# valkuil bij self-updating scripts). scripts/provision.sh wordt daarentegen
# als los proces gestart ná de checkout, en wordt dus altijd vers en volledig
# van schijf gelezen.

REPO="https://github.com/malbregt/mtd-agent.git"
INSTALL_DIR="/opt/mtd-agent"
TARGET_VERSION="${1:-}"

echo "=== MTD Agent Installer ==="

echo "[1/2] Systeem updaten..."
apt-get update -qq
apt-get install -y -qq python3 python3-pip python3-venv git curl sqlite3

echo "[2/2] Agent downloaden..."
OLD_COMMIT=""
if [ -d "$INSTALL_DIR/.git" ]; then
  cd $INSTALL_DIR
  OLD_COMMIT=$(git rev-parse HEAD)
  git fetch --tags origin
  if [ -n "$TARGET_VERSION" ]; then
    git checkout "$TARGET_VERSION"
  else
    git checkout master
    git pull
  fi
else
  git clone $REPO $INSTALL_DIR
  if [ -n "$TARGET_VERSION" ]; then
    cd $INSTALL_DIR && git checkout "$TARGET_VERSION"
  fi
fi

exec bash "$INSTALL_DIR/scripts/provision.sh" "$TARGET_VERSION" "$OLD_COMMIT"
