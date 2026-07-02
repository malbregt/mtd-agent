# MTD Agent

Edge agent voor het mijnthuisdata platform. Draait op een Raspberry Pi bij de klant thuis en leest lokale energie-APIs.

## Installatie

Op een verse Raspberry Pi OS Lite installatie:

```bash
curl -sSL https://raw.githubusercontent.com/malbregt/mtd-agent/main/install.sh | bash
```

## Configuratie

Pas `/opt/mtd-agent/config.json` aan na installatie:

```json
{
  "api_key": "jouw-mtd-api-key",
  "integrations": [
    {
      "type": "homewizard_p1",
      "name": "P1 Meter",
      "host": "192.168.1.50"
    }
  ]
}
```

Herstart daarna de service:

```bash
sudo systemctl restart mtd-agent
```

## Beheer

```bash
# Status
sudo systemctl status mtd-agent

# Logs volgen
sudo journalctl -u mtd-agent -f

# Handmatig herstarten
sudo systemctl restart mtd-agent
```

## Structuur

```
agent/
├── main.py                    # Entry point
├── config.py                  # Config manager
├── sync.py                    # SQLite cache + API sync
└── integrations/
    ├── base.py                # BaseIntegration
    └── homewizard.py          # HomeWizard P1
```

## Ondersteunde integraties

| Type | Status |
|------|--------|
| `homewizard_p1` | ✓ |
| `enphase_envoy` | Gepland |
| `dsmr` | Gepland |
