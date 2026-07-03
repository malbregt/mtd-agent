# MTD Agent

Edge agent voor het [mijnthuisdata](https://mijnthuisdata.nl) platform. Draait op een Raspberry Pi bij de klant thuis en verzamelt lokale energiedata.

## Installatie

Op een verse Raspberry Pi OS Lite installatie:

```bash
curl -sSL https://raw.githubusercontent.com/malbregt/mtd-agent/main/install.sh | sudo bash
```

**Eerste installatie:** de Pi start automatisch een hotspot `MTD-Setup`. Verbind hiermee en configureer het apparaat via de captive portal.

**Update:** het script detecteert een bestaande installatie en herstart alleen de agent.

## Onboarding

1. Pi opgestart → hotspot `MTD-Setup` verschijnt
2. Verbind met `MTD-Setup` (geen wachtwoord)
3. Captive portal opent automatisch (of ga naar `192.168.4.1`)
4. Kies methode: API Key / Inloggen / QR Code
5. Vul eventueel WiFi netwerk in
6. Klik "Apparaat koppelen"
7. Pi herstart en verbindt met het platform

## Structuur

```
mtd-agent/
├── install.sh                    # Bootstrap script
├── requirements.txt
├── config.example.json
├── agent/
│   ├── main.py                   # Entry point + hoofdloop
│   ├── config.py                 # Config manager
│   ├── api.py                    # Platform API client
│   ├── sync.py                   # SQLite cache + sync
│   ├── websocket_client.py       # WebSocket voor config push
│   ├── plugin_manager.py         # Dynamisch laden van plugins
│   └── integrations/
│       ├── base.py               # BaseIntegration
│       ├── homewizard_p1.py      # HomeWizard P1 plugin
│       ├── homewizard_water.py   # HomeWizard Watermeter plugin
│       └── enphase.py            # Enphase Envoy plugin
├── captive_portal/
│   ├── portal.py                 # Flask onboarding portal
│   └── templates/
│       └── index.html            # Portal UI
├── systemd/
│   ├── mtd-agent.service         # Agent service
│   └── mtd-portal.service        # Portal service
└── scripts/
    └── setup-hotspot.sh          # WiFi hotspot instellen
```

## Plugins

Plugins worden dynamisch geladen op basis van de config van het platform. Bij een nieuw apparaattype downloadt de Pi automatisch de benodigde plugin van GitHub.

Elke plugin erft van `BaseIntegration` en implementeert de `poll()` methode.

## Beheer

```bash
sudo systemctl status mtd-agent
sudo journalctl -u mtd-agent -f
sudo systemctl restart mtd-agent
```
