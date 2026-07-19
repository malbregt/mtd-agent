import asyncio
from dataclasses import dataclass, field
from typing import Any


@dataclass
class Event:
    topic: str          # "reading" | "health" | "command"
    payload: Any


class Bus:
    """Interne pub/sub bus tussen plugins/supervisor en de sync-loop.
    Eén gedeelde asyncio.Queue per topic-abonnee — bewust simpel gehouden
    (geen externe dependency) omdat alles binnen één proces/event loop draait."""

    def __init__(self):
        self._subscribers: dict[str, list[asyncio.Queue]] = {}

    def subscribe(self, topic: str) -> asyncio.Queue:
        q: asyncio.Queue = asyncio.Queue()
        self._subscribers.setdefault(topic, []).append(q)
        return q

    async def publish(self, topic: str, payload: Any) -> None:
        for q in self._subscribers.get(topic, []):
            await q.put(Event(topic=topic, payload=payload))
