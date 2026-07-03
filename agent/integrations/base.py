from abc import ABC, abstractmethod


class BaseIntegration(ABC):
    def __init__(self, integration_id: str, config: dict, sync, api_client):
        self.integration_id = integration_id
        self.config = config
        self.sync = sync
        self.api = api_client
        self.name = config.get("name", self.__class__.__name__)
        self.poll_interval = config.get("poll_interval", 60)
        self._error_count = 0

    @abstractmethod
    def poll(self):
        """Lees data en sla op via sync.store()"""
        pass

    def report_error(self, message: str):
        self._error_count += 1
        self.api.send_event(self.integration_id, "error", message)

    def report_ok(self):
        if self._error_count > 0:
            self._error_count = 0
            self.api.send_event(self.integration_id, "info", "Integratie hersteld")

    @staticmethod
    def test_connection(config: dict) -> dict:
        """Voer eenmalige verbindingstest uit met de gegeven config (nog niet opgeslagen).

        Gooit een Exception met leesbare foutmelding bij falen.
        Retourneert optioneel een dict met device-info voor in de UI.
        """
        raise NotImplementedError("Verbindingstest niet ondersteund voor deze integratie")
