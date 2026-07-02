import json
import logging
import os

logger = logging.getLogger("config")

CONFIG_PATH = os.environ.get("MTD_CONFIG", "/opt/mtd-agent/config.json")


class ConfigManager:
    def __init__(self):
        self._config = {}
        self.load()

    def load(self):
        try:
            with open(CONFIG_PATH) as f:
                self._config = json.load(f)
            logger.info("Config geladen")
        except FileNotFoundError:
            logger.warning(f"Config niet gevonden op {CONFIG_PATH}, gebruik lege config")
        except json.JSONDecodeError as e:
            logger.error(f"Ongeldige config JSON: {e}")

    def get(self, key, default=None):
        return self._config.get(key, default)
