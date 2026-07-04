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
CORE_CHANGED=true  # bij eerste installatie: altijd core starten
LEGACY_MIGRATION=false
if systemctl list-unit-files | grep -q "^${LEGACY_SERVICE}.service"; then
  LEGACY_MIGRATION=true
fi

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
  if [ "$OLD_COMMIT" != "$NEW_COMMIT" ] && [ "$LEGACY_MIGRATION" = false ]; then
    if git diff --name-only "$OLD_COMMIT" "$NEW_COMMIT" | grep -qE "$CORE_FILE_PATTERN"; then
      CORE_CHANGED=true
    else
      CORE_CHANGED=false
    fi
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

  # Nieuwe services eerst starten: dit zijn onafhankelijke systemd-units die
  # blijven draaien ook als dit script zelf zo dadelijk wordt afgebroken
  # (zie migratiestap hieronder).
  systemctl restart $SERVICE_WORKER

  # Core alleen herstarten als core-bestanden ook echt gewijzigd zijn (of bij
  # een migratie), zodat de statuspagina/heartbeat bij een gewone integratie-
  # /workerfix online blijft.
  if [ "$CORE_CHANGED" = true ] || [ "$LEGACY_MIGRATION" = true ]; then
    systemctl restart $SERVICE_CORE
  else
    echo "  Geen core-wijzigingen gedetecteerd, mtd-core blijft doorlopen."
  fi

  # Migratie: oude alles-in-1 service pas NU opruimen, als allerlaatste stap.
  # Als deze update zelf via die oude mtd-agent.service is getriggerd, draait
  # dit script als kindproces daarvan - 'systemctl stop' hierop kan de hele
  # cgroup (dus ook dit script) meteen afbreken. Dat mag pas gebeuren nadat
  # mtd-core/mtd-worker al zelfstandig draaien, anders eindigt het apparaat
  # zonder enige actieve service.
  if [ "$LEGACY_MIGRATION" = true ]; then
    echo "  Migratie: oude ${LEGACY_SERVICE}.service opruimen..."
    systemctl disable ${LEGACY_SERVICE} 2>/dev/null || true
    rm -f /etc/systemd/system/${LEGACY_SERVICE}.service
    systemctl daemon-reload
    systemctl stop ${LEGACY_SERVICE} 2>/dev/null || true
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
