import importlib.util
import logging
import os
import requests

logger = logging.getLogger("plugins")

PLUGIN_DIR = os.environ.get("MTD_PLUGIN_DIR", "/opt/mtd-agent/agent/integrations")
PLUGIN_BASE_URL = os.environ.get("MTD_PLUGIN_URL", "https://raw.githubusercontent.com/malbregt/mtd-agent/main/agent/integrations")


class PluginManager:
    def __init__(self):
        self._plugins = {}

    def load(self, plugin_name: str):
        """Laad een plugin op naam (bijv. 'homewizard')."""
        if plugin_name in self._plugins:
            return self._plugins[plugin_name]

        path = os.path.join(PLUGIN_DIR, f"{plugin_name}.py")

        if not os.path.exists(path):
            logger.warning(f"Plugin {plugin_name} niet gevonden, downloaden...")
            if not self._download(plugin_name, path):
                return None

        try:
            spec = importlib.util.spec_from_file_location(plugin_name, path)
            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)
            self._plugins[plugin_name] = module
            logger.info(f"Plugin geladen: {plugin_name}")
            return module
        except Exception as e:
            logger.error(f"Plugin laden mislukt ({plugin_name}): {e}")
            return None

    def _download(self, plugin_name: str, path: str) -> bool:
        """Download plugin van GitHub."""
        url = f"{PLUGIN_BASE_URL}/{plugin_name}.py"
        try:
            resp = requests.get(url, timeout=10)
            if resp.status_code == 200:
                os.makedirs(os.path.dirname(path), exist_ok=True)
                with open(path, "w") as f:
                    f.write(resp.text)
                logger.info(f"Plugin gedownload: {plugin_name}")
                return True
            else:
                logger.error(f"Plugin download mislukt: HTTP {resp.status_code}")
        except requests.RequestException as e:
            logger.error(f"Plugin download fout: {e}")
        return False

    def get_integration_class(self, plugin_name: str):
        """Retourneert de integratie class uit een plugin."""
        module = self.load(plugin_name)
        if not module:
            return None
        # Conventie: class heet bijv. HomewizardIntegration
        class_name = plugin_name.replace("_", " ").title().replace(" ", "") + "Integration"
        return getattr(module, class_name, None)
