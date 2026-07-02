import asyncio
import json
import logging
import os
import websockets

logger = logging.getLogger("websocket")

WS_URL = os.environ.get("MTD_WS_URL", "wss://api.mijnthuisdata.nl/agent/ws")
RECONNECT_DELAY = 10


class WebSocketClient:
    def __init__(self, config, on_message):
        self.config = config
        self.on_message = on_message  # callback voor binnenkomende berichten
        self._running = False

    async def run(self):
        self._running = True
        while self._running:
            try:
                api_key = self.config.get("api_key")
                if not api_key:
                    logger.warning("Geen API key, WebSocket uitgesteld")
                    await asyncio.sleep(RECONNECT_DELAY)
                    continue

                logger.info("WebSocket verbinding openen...")
                async with websockets.connect(
                    WS_URL,
                    extra_headers={"X-API-Key": api_key}
                ) as ws:
                    logger.info("WebSocket verbonden")
                    async for message in ws:
                        try:
                            data = json.loads(message)
                            await self.on_message(data)
                            await ws.send(json.dumps({"type": "ack"}))
                        except json.JSONDecodeError:
                            logger.warning(f"Ongeldig WebSocket bericht: {message}")

            except Exception as e:
                logger.warning(f"WebSocket verbinding verbroken: {e}")

            logger.info(f"Herverbinden over {RECONNECT_DELAY} seconden...")
            await asyncio.sleep(RECONNECT_DELAY)

    def stop(self):
        self._running = False
