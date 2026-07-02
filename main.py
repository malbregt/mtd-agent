import time
import logging
from config import ConfigManager
from sync import SyncWorker
from integrations.homewizard import HomeWizardIntegration

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s"
)
logger = logging.getLogger("mtd-agent")


def main():
    logger.info("MTD Agent gestart")

    config = ConfigManager()
    sync = SyncWorker(config)

    # Laad actieve integraties op basis van config
    integrations = []
    for item in config.get("integrations", []):
        if item["type"] == "homewizard_p1":
            integrations.append(HomeWizardIntegration(item, sync))
        # Voeg hier andere integraties toe

    if not integrations:
        logger.warning("Geen integraties geconfigureerd.")

    logger.info(f"{len(integrations)} integratie(s) actief")

    while True:
        for integration in integrations:
            try:
                integration.poll()
            except Exception as e:
                logger.error(f"Fout in {integration.name}: {e}")
        time.sleep(10)


if __name__ == "__main__":
    main()
