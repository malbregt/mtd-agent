# MTD Agent

Edge agent voor het [mijnthuisdata](https://mijnthuisdata.nl) platform. Draait op een Raspberry Pi bij de klant thuis en verzamelt lokale energiedata.

## Installatie

Op een verse Raspberry Pi OS Lite installatie, met een device-id en agent-token die het platform al heeft uitgegeven:

```bash
curl -fsSL https://raw.githubusercontent.com/malbregt/mtd-agent/v2-async-rebuild/install.sh \
  | sudo bash -s -- \
      --agent-key mtd_agent_xxxxxxxx \
      --plugin p1_serial \
      --plugin-config '{"port":"/dev/ttyUSB0","baudrate":115200,"collect_interval_s":10}'
```

Dit installeert de agent, schrijft het token weg en start de service direct — geen aparte onboarding-stap. Ontbreekt het token nog (of is het gewijzigd), dan is dat achteraf in te vullen via de lokale statuspagina (`http://<pi-ip>:8080`), via het veld "Agent-token" — dit herstart de service automatisch met de nieuwe waarde.

**Update:** `scripts/update.sh` wordt door het platform op afstand getriggerd (via `core/sync.py`) en checkt uit naar de opgegeven git-tag, met een sanity-check en automatische terugval naar de vorige versie bij een mislukte update.

## Structuur

```
mtd-agent/
├── install.sh                    # Eén-commando installatie (device-id/token al bekend bij platform)
├── main.py                       # Entrypoint: bootstrap agent + lokale webserver
├── config.py                     # Config (env-variabelen)
├── requirements.txt
├── core/
│   ├── agent.py                  # Bootstrap, plugin-lifecycle, config-push, commands
│   ├── database.py                # SQLite: device-config, plugins, readings
│   ├── env_file.py                # Schrijft AGENT_KEY naar /etc/mtd-agent/env
│   ├── plugin.py                  # DevicePlugin/Reading/Command basisklassen
│   ├── plugin_download.py         # OTA-download van losse plugins (GitHub)
│   ├── supervisor.py              # Start/stop/herstart van plugin-taken
│   ├── sync.py                    # WebSocket-verbinding met het platform
│   └── health.py                  # Status per plugin (voor statuspagina)
├── plugins/                       # Vendored plugins (HomeWizard, SolarEdge, Enphase, ...)
├── web/
│   ├── server.py                  # Lokale FastAPI-statuspagina + /api/token
│   └── static/                    # Statuspagina UI (zie screenshot in het beheerpaneel)
├── systemd/
│   └── mtd-agent.service          # Enige service — geen aparte onboarding/portal-service meer
└── scripts/
    └── update.sh                  # OTA core-update, op afstand getriggerd door het platform
```

## Plugins

Plugins worden dynamisch geladen op basis van de config die het platform pusht. Bij een nieuwe/gewijzigde plugin-versie downloadt de Pi automatisch de benodigde bestanden van GitHub.

Elke plugin erft van `DevicePlugin` (`core/plugin.py`) en implementeert `poll()`.

## Beheer

```bash
sudo systemctl status mtd-agent
sudo journalctl -u mtd-agent -f
```
