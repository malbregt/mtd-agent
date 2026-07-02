from abc import ABC, abstractmethod


class BaseIntegration(ABC):
    def __init__(self, config: dict, sync):
        self.config = config
        self.sync = sync
        self.name = config.get("name", self.__class__.__name__)

    @abstractmethod
    def poll(self):
        """Lees data van de integratie en sla op via sync.store()"""
        pass
