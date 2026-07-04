#!/bin/bash
set -e

REPO="https://github.com/malbregt/mtd-agent.git"
INSTALL_DIR="/opt/mtd-agent"
SERVICE_CORE="mtd-core"
SERVICE_WORKER="mtd-worker"
SERVICE_PORTAL="mtd-portal"
LEGACY_SERVICE="mtd-agent"
TARGET_VERSION="${1:-}"

# Bestanden die core-gedrag bepalen (heartbeat/WS/OTA/statuspagina). Als een
# update alleen buiten deze lijst wijzigt (bijv. een integratie-plugin of
# worker-logica), blijft mtd-core gewoon doorlopen — statuspagina en heartbeat
# blijven dan bereikbaar tijdens de update.
CORE_FILE_PATTERN='^(agent/core\.py|agent/api\.py|agent/websocket_client\.py|agent/status_server\.py|agent/state\.py|agent/signals\.py|agent/version\.py|install\.sh|systemd/)'

echo "=== MTD Agent Installer ==="

# 1. Systeem updaten
echo "[1/6] Systeem updaten..."
apt-get update -qq
apt-get install -y -qq python3 python3-pip python3-venv git curl sqlite3

# 2. Agent downloaden of updaten
echo "[2/6] Agent downloaden..."
CORE_CHANGED=true  # bij eerste installatie of migratie: altijd core (opnieuw) starten
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
  NEW_COMMIT=$(git rev-parse HEAD)
  if [ "$OLD_COMMIT" != "$NEW_COMMIT" ]; then
    if git diff --name-only "$OLD_COMMIT" "$NEW_COMMIT" | grep -qE "$CORE_FILE_PATTERN"; then
      CORE_CHANGED=true
    else
      CORE_CHANGED=false
    fi
  else
    CORE_CHANGED=false
  fi
else
  git clone $REPO $INSTALL_DIR
  if [ -n "$TARGET_VERSION" ]; then
    cd $INSTALL_DIR && git checkout "$TARGET_VERSION"
  fi
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
cp $INSTALL_DIR/systemd/mtd-core.service /etc/systemd/system/
cp $INSTALL_DIR/systemd/mtd-worker.service /etc/systemd/system/
cp $INSTALL_DIR/systemd/mtd-portal.service /etc/systemd/system/

# Migratie: oude alles-in-1 service (vóór de core/worker-splitsing) opruimen
# als die nog op dit apparaat draait.
if systemctl list-unit-files | grep -q "^${LEGACY_SERVICE}.service"; then
  echo "  Migreren van oude ${LEGACY_SERVICE}.service naar losse mtd-core/mtd-worker services..."
  systemctl stop ${LEGACY_SERVICE} 2>/dev/null || true
  systemctl disable ${LEGACY_SERVICE} 2>/dev/null || true
  rm -f /etc/systemd/system/${LEGACY_SERVICE}.service
  CORE_CHANGED=true
fi

systemctl daemon-reload

# 6. Starten
echo "[6/6] Starten..."
if [ "$FIRST_INSTALL" = true ]; then
  echo "Eerste installatie — captive portal starten..."
  bash $INSTALL_DIR/scripts/setup-hotspot.sh
  systemctl enable $SERVICE_PORTAL
  systemctl start $SERVICE_PORTAL
else
  systemctl enable $SERVICE_CORE
  systemctl enable $SERVICE_WORKER
  systemctl disable $SERVICE_PORTAL 2>/dev/null || true
  systemctl stop $SERVICE_PORTAL 2>/dev/null || true

  # Worker mag altijd herstarten - dat raakt nooit de statuspagina/heartbeat.
  systemctl restart $SERVICE_WORKER

  # Core alleen herstarten als core-bestanden ook echt gewijzigd zijn, zodat de
  # statuspagina/heartbeat bij een gewone integratie-/workerfix online blijft.
  if [ "$CORE_CHANGED" = true ]; then
    systemctl restart $SERVICE_CORE
  else
    echo "  Geen core-wijzigingen gedetecteerd, mtd-core blijft doorlopen."
  fi
fi

echo ""
echo "✓ MTD Agent geinstalleerd."
if [ "$FIRST_INSTALL" = true ]; then
  echo "  Verbind met WiFi netwerk 'MTD-Setup' om het apparaat te configureren."
else
  echo "  Status: sudo systemctl status mtd-core mtd-worker"
  echo "  Logs:   sudo journalctl -u mtd-core -f   (of -u mtd-worker)"
fi
