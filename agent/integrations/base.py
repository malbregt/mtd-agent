from abc import ABC, abstractmethod
from collections import deque
from datetime import datetime, timezone

MAX_RECENT_ERRORS = 20


class BaseIntegration(ABC):
    def __init__(self, integration_id: str, config: dict, sync, api_client):
        self.customer_integration_id = integration_id
        self.integration_id = config.get("type", integration_id)
        self.config = config
        self.sync = sync
        self.api = api_client
        self.name = config.get("name", self.__class__.__name__)
        self.poll_interval = config.get("poll_interval", 60)
        self._error_count = 0
        self._recent_errors = deque(maxlen=MAX_RECENT_ERRORS)

    @abstractmethod
    def poll(self):
        """Lees data en sla op via sync.store()"""
        pass

    def report_error(self, message: str):
        self._error_count += 1
        self._recent_errors.append({
            "time": datetime.now(timezone.utc).isoformat(),
            "message": message,
        })
        self.api.send_event(self.integration_id, "error", message)

    def report_ok(self):
        if self._error_count > 0:
            self._error_count = 0
            self.api.send_event(self.integration_id, "info", "Integratie hersteld")

    @staticmethod
    def normalize_host(host: str, default_scheme: str = "http") -> str:
        """Zorg dat host altijd een schema heeft, ongeacht of de gebruiker het zelf invulde."""
        if not host:
            return host
        if "://" not in host:
            return f"{default_scheme}://{host}"
        return host

    @staticmethod
    def test_connection(config: dict) -> dict:
        """Voer eenmalige verbindingstest uit met de gegeven config (nog niet opgeslagen).

        Gooit een Exception met leesbare foutmelding bij falen.
        Retourneert optioneel een dict met device-info voor in de UI.
        """
        raise NotImplementedError("Verbindingstest niet ondersteund voor deze integratie")
