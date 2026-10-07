"""WebSocket hub: pushes live events, strain updates and plan changes to every open dashboard.

The MQTT subscriber runs in paho's network thread, but WebSockets live in the asyncio event
loop. publish_threadsafe() hands a message from that thread to the loop safely.
"""
import asyncio
import json
import logging

from fastapi.encoders import jsonable_encoder

log = logging.getLogger("backend.live")


class LiveHub:
    def __init__(self):
        self.clients = set()
        self.loop = None

    async def connect(self, websocket):
        await websocket.accept()
        self.clients.add(websocket)

    def disconnect(self, websocket):
        self.clients.discard(websocket)

    async def broadcast(self, message):
        text = json.dumps(jsonable_encoder(message))
        for ws in list(self.clients):
            try:
                await ws.send_text(text)
            except Exception:                 # closed tab, network error ...
                self.clients.discard(ws)

    def publish_threadsafe(self, message):
        """Call from any thread (e.g. the MQTT callback)."""
        if self.loop is not None and self.clients:
            asyncio.run_coroutine_threadsafe(self.broadcast(message), self.loop)
