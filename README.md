# MTD Agent

Edge agent voor het [mijnthuisdata](https://mijnthuisdata.nl) platform. Draait op een Raspberry Pi bij de klant thuis en verzamelt lokale energiedata.

## Installatie

Op een verse Raspberry Pi OS Lite installatie volstaat één kaal commando — geen argumenten nodig:

```bash
curl -fsSL https://raw.githubusercontent.com/malbregt/mtd-agent/v2-async-rebuild/install.sh \
  | sudo bash
```

Dit installeert de agent en start de service direct — geen aparte onboarding-stap. De hostname wordt automatisch uniek gemaakt met een stukje CPU-serienummer (bv. `http://mtd-bridge-a3f2.local:8080`), zodat meerdere bridges op hetzelfde netwerk niet botsen — override met `--hostname` voor een eigen naam. Token en plugin(s) koppel je daarna:
- **Token:** via het veld "Agent-token" op de lokale statuspagina — herstart de service automatisch met de nieuwe waarde.
- **Plugin(s):** via het platform (config-push naar het device zodra het gekoppeld is).

Voor scripted rollouts kun je `--agent-key`/`--plugin`/`--plugin-config` ook meteen meegeven aan `install.sh` (zie de comments bovenin het script), maar dat is niet de standaardroute.

**Update:** `scripts/update.sh` wordt door het platform op afstand getriggerd (via `core/sync.py`) en checkt uit naar de opgegeven git-tag, met een sanity-check en automatische terugval naar de vorige versie bij een mislukte update.

## Structuur

```
mtd-agent/
├── install.sh                    # Eén-commando installatie, alle argumenten optioneel
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
