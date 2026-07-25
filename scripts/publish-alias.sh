#!/bin/bash
# Publiceert een tweede, per-Pi-uniek mDNS-adres (mtd-<serial>.local) náást de
# primaire hostname (default mtd-bridge.local, of --hostname bij install).
# Nodig omdat je bij het tegelijk opstarten van meerdere kale bridges (nog
# vóór je elk apart een --hostname geeft) anders allemaal op dezelfde
# mtd-bridge.local botst — dit tweede adres is altijd uniek, gebaseerd op de
# CPU-serial van de Pi zelf, dus onafhankelijk van hostname-configuratie of
# SD-kaart-kloon.
#
# avahi-publish blijft als voorgrondproces draaien zolang de registratie
# geldig moet zijn; systemd (Restart=always) start 'm opnieuw op bij netwerk-
# wisselingen zodat het IP-adres in de registratie actueel blijft.
set -euo pipefail

SERIAL=$(awk -F': ' '/^Serial/ {print $2}' /proc/cpuinfo 2>/dev/null | tr -d ' \n')
if [ -z "$SERIAL" ]; then
  # Geen Raspberry Pi CPU-serial beschikbaar (bv. andere hardware) — val terug
  # op machine-id, ook uniek per systeem.
  SERIAL=$(cat /etc/machine-id)
fi
SHORT_ID="${SERIAL: -6}"
ALIAS_HOSTNAME="mtd-${SHORT_ID}"

IP=$(hostname -I | awk '{print $1}')
if [ -z "$IP" ]; then
  echo "Geen IP-adres gevonden, kan alias niet publiceren" >&2
  exit 1
fi

echo "Publiceer $ALIAS_HOSTNAME.local -> $IP"
exec avahi-publish -a -R "${ALIAS_HOSTNAME}.local" "$IP"
