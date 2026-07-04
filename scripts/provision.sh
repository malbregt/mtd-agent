#!/bin/bash
set -e

# Wordt aangeroepen door install.sh, NA de git checkout, als los proces (zie
# de uitleg bovenaan install.sh voor waarom dat belangrijk is). Bevat alle
# eigenlijke installatie-/update-logica en mag dus vrij wijzigen tussen
# versies zonder het self-modifying-script risico van install.sh zelf.

INSTALL_DIR="/opt/mtd-agent"
SERVICE_CORE="mtd-core"
SERVICE_WORKER="mtd-worker"
SERVICE_PORTAL="mtd-portal"
LEGACY_SERVICE="mtd-agent"
TARGET_VERSION="${1:-}"
OLD_COMMIT="${2:-}"

# Bestanden die core-gedrag bepalen (heartbeat/WS/OTA/statuspagina/installatie
# zelf). Als een update alleen buiten deze lijst wijzigt (bijv. een
# integratie-plugin of worker-logica), blijft mtd-core gewoon doorlopen -
# statuspagina en heartbeat blijven dan bereikbaar tijdens de update.
CORE_FILE_PATTERN='^(agent/core\.py|agent/api\.py|agent/websocket_client\.py|agent/status_server\.py|agent/state\.py|agent/signals\.py|agent/version\.py|install\.sh|scripts/provision\.sh|systemd/)'

echo "=== MTD Agent Provisioning ==="

CORE_CHANGED=true  # bij eerste installatie: altijd core starten
LEGACY_MIGRATION=false
if systemctl list-unit-files | grep -q "^${LEGACY_SERVICE}.service"; then
  LEGACY_MIGRATION=true
fi

NEW_COMMIT=$(cd "$INSTALL_DIR" && git rev-parse HEAD)
if [ -n "$OLD_COMMIT" ] && [ "$OLD_COMMIT" != "$NEW_COMMIT" ] && [ "$LEGACY_MIGRATION" = false ]; then
  if (cd "$INSTALL_DIR" && git diff --name-only "$OLD_COMMIT" "$NEW_COMMIT") | grep -qE "$CORE_FILE_PATTERN"; then
    CORE_CHANGED=true
  else
    CORE_CHANGED=false
  fi
elif [ -z "$OLD_COMMIT" ]; then
  CORE_CHANGED=false
fi

# 1. Python omgeving
echo "[1/4] Python omgeving aanmaken..."
python3 -m venv $INSTALL_DIR/venv
$INSTALL_DIR/venv/bin/pip install -q -r $INSTALL_DIR/requirements.txt

# 2. Config aanmaken indien niet aanwezig
if [ ! -f $INSTALL_DIR/config.json ]; then
  echo "[2/4] Standaard config aanmaken..."
  cp $INSTALL_DIR/config.example.json $INSTALL_DIR/config.json
  FIRST_INSTALL=true
else
  echo "[2/4] Config al aanwezig, overslaan."
  FIRST_INSTALL=false
fi

# 3. Systemd services installeren
echo "[3/4] Services installeren..."
cp $INSTALL_DIR/systemd/mtd-core.service /etc/systemd/system/
cp $INSTALL_DIR/systemd/mtd-worker.service /etc/systemd/system/
cp $INSTALL_DIR/systemd/mtd-portal.service /etc/systemd/system/
systemctl daemon-reload

# 4. Starten
echo "[4/4] Starten..."
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
