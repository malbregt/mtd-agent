#!/bin/bash
# OTA-update van de agent-kern. Wordt NIET rechtstreeks vanuit de working
# tree aangeroepen — core/sync.py::_trigger_update() kopieert dit bestand
# eerst naar /tmp en voert die kopie uit. Reden: dit script doet zelf een
# `git checkout` in /opt/mtd-agent, en als het in-place vanuit die working
# tree bleef draaien, zou bash halverwege kunnen overschakelen op de nieuwe
# (nog niet klaar geladen) scriptinhoud — een mix van oude/nieuwe code
# uitvoeren. Vanuit /tmp draaiend is dit script zelf geen onderdeel meer van
# wat git zojuist overschreven heeft.
#
# Volgorde is bewust: nooit de service stoppen vóórdat dit script klaar is
# (anders killt dat de update halverwege) — de systemd-herstart is altijd de
# allerlaatste stap.
set -uo pipefail

INSTALL_DIR="/opt/mtd-agent"
TARGET_VERSION="${1:?gebruik: update.sh <git-tag>}"

cd "$INSTALL_DIR" || exit 1
PREV_COMMIT=$(git rev-parse HEAD)

echo "=== MTD Agent OTA: bijwerken naar $TARGET_VERSION (huidig: $PREV_COMMIT) ==="

report_failure() {
  local error_msg="$1"
  echo "FOUT: $error_msg" >&2
  curl -s -X POST "${PLATFORM_API_URL:-https://api.mijnthuisdata.nl}/agent/update-result" \
    -H "X-Api-Key: ${AGENT_KEY:-}" -H "Content-Type: application/json" \
    -d "{\"success\": false, \"version\": \"$TARGET_VERSION\", \"error\": \"$error_msg\"}" \
    >/dev/null 2>&1 || true
}

if ! git fetch --tags 2>&1; then
  report_failure "git fetch mislukt"
  exit 1
fi

if ! git checkout "$TARGET_VERSION" 2>&1; then
  report_failure "git checkout naar $TARGET_VERSION mislukt"
  git checkout "$PREV_COMMIT" 2>&1
  exit 1
fi

if ! "$INSTALL_DIR/venv/bin/pip" install -q -r requirements.txt 2>&1; then
  report_failure "pip install mislukt na checkout naar $TARGET_VERSION"
  git checkout "$PREV_COMMIT" 2>&1
  systemctl restart mtd-agent
  exit 1
fi

# Sanity-check: nooit een kapotte release actief laten worden. Bewust breed
# (alle .py-bestanden), een syntaxfout waar dan ook zou de agent na herstart
# meteen laten crashen.
if ! "$INSTALL_DIR/venv/bin/python" -c "
import py_compile, pathlib, sys
failed = False
for f in pathlib.Path('.').rglob('*.py'):
    if 'venv' in f.parts:
        continue
    try:
        py_compile.compile(str(f), doraise=True)
    except py_compile.PyCompileError as e:
        print(e, file=sys.stderr)
        failed = True
sys.exit(1 if failed else 0)
"; then
  report_failure "py_compile sanity-check mislukt voor $TARGET_VERSION, teruggedraaid naar $PREV_COMMIT"
  git checkout "$PREV_COMMIT" 2>&1
  systemctl restart mtd-agent
  exit 1
fi

echo "Sanity-check OK, agent herstarten..."
systemctl restart mtd-agent
# Succes wordt niet hier gerapporteerd (dit proces/de herstartende service
# overleeft dit sowieso niet netjes) — de heartbeat na herstart met de nieuwe
# agent_version bevestigt succes bij het platform (zie _apply_heartbeat).
