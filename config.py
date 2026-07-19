import os

PLATFORM_WS_URL = os.getenv("PLATFORM_WS_URL", "wss://api.mijnthuisdata.nl/agent/ws")
PLATFORM_API_URL = os.getenv("PLATFORM_API_URL", "https://api.mijnthuisdata.nl")
AGENT_KEY = os.getenv("AGENT_KEY", "")
DB_PATH = os.getenv("DB_PATH", "/data/agent.db")
PLUGIN_DIR = os.getenv("PLUGIN_DIR", "/data/plugins")
PLUGIN_REPO = os.getenv("PLUGIN_REPO", "malbregt/mtd-agent")
WEB_PORT = int(os.getenv("WEB_PORT", "8080"))
LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO")

# Interne timing-constanten
READINGS_FLUSH_INTERVAL_S = 30
HEALTH_FLUSH_INTERVAL_S = 60
WS_RECONNECT_DELAY_S = 10
RESTART_BACKOFF_S = (30, 60, 120)
MAX_RESTART_ATTEMPTS = 5
# Vangnet naast de event-driven config_update-pushes: als zo'n signaal om wat
# voor reden dan ook nooit aankomt (gemiste race, kortstondige WS-hapering),
# haalt de agent periodiek gewoon opnieuw de volledige config op.
CONFIG_REFRESH_INTERVAL_S = 900
