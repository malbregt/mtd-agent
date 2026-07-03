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
            logger.warning(f"Config niet gevonden op {CONFIG_PATH}")
        except json.JSONDecodeError as e:
            logger.error(f"Ongeldige config JSON: {e}")

    def save(self, data: dict):
        self._config.update(data)
        with open(CONFIG_PATH, "w") as f:
            json.dump(self._config, f, indent=2)
        logger.info("Config opgeslagen")

    def get(self, key, default=None):
        return self._config.get(key, default)

    def set(self, key, value):
        if self._config.get(key) == value:
            return
        self._config[key] = value
        self.save(self._config)
