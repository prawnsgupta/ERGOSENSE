"""MQTT subscriber: broker -> validate -> PostgreSQL (idempotent) -> live update callback.

Delivery guarantees, end to end:
  * Subscribed with QoS 1 under a fixed client id and clean_session=False (a persistent
    session): while the backend is down, the broker keeps queuing our events.
  * manual_ack=True: we send the PUBACK only AFTER the database transaction has committed.
    If the backend crashes in between, the broker re-delivers the event later instead of
    losing it ("at-least-once").
  * Re-delivered or duplicated events are harmless because posture_events.event_id is the
    primary key and the strain update happens in the same transaction as the insert.
"""
import logging
import threading
import time

import paho.mqtt.client as mqtt
import psycopg

from ergosense import config
from ergosense.backend import repository
from ergosense.backend.db import get_pool
from ergosense.events import InvalidEvent, decode

log = logging.getLogger("backend.ingest")


class EventIngestor:
    def __init__(self, on_ingested=None, client_id="ergosense-backend"):
        self.on_ingested = on_ingested          # called with {"event": ..., "strain": ...}
        self.connected = False
        self.stats = {"received": 0, "stored": 0, "skipped": 0, "rejected": 0}   # skipped = duplicate or no matching shift/worker
        self._stopping = threading.Event()
        self.client = mqtt.Client(
            callback_api_version=mqtt.CallbackAPIVersion.VERSION2,
            client_id=client_id,
            clean_session=False,
            manual_ack=True,
        )
        self.client.on_connect = self._on_connect
        self.client.on_disconnect = self._on_disconnect
        self.client.on_message = self._on_message
        self.client.reconnect_delay_set(min_delay=1, max_delay=10)

    def start(self):
        self.client.connect_async(config.MQTT_HOST, config.MQTT_PORT, keepalive=30)
        self.client.loop_start()

    def stop(self):
        self._stopping.set()
        self.client.loop_stop()
        self.client.disconnect()

    def _on_connect(self, client, userdata, flags, reason_code, properties):
        if reason_code.is_failure:
            log.warning("broker refused connection: %s", reason_code)
            return
        self.connected = True
        client.subscribe(f"{config.TOPIC_PREFIX}/+", qos=1)
        log.info("subscribed to %s/+ (session present: %s)", config.TOPIC_PREFIX, flags.session_present)

    def _on_disconnect(self, client, userdata, flags, reason_code, properties):
        self.connected = False
        if not self._stopping.is_set():
            log.warning("disconnected from broker (%s), paho will reconnect", reason_code)

    def _on_message(self, client, userdata, msg):
        self.stats["received"] += 1
        try:
            event = decode(msg.payload)
        except InvalidEvent as exc:
            log.warning("rejected message on %s: %s", msg.topic, exc)
            self.stats["rejected"] += 1
            client.ack(msg.mid, msg.qos)                # a malformed message will never become valid
            return

        result = self._store_with_retry(event)
        client.ack(msg.mid, msg.qos)                    # only after the commit
        if result is None:
            self.stats["skipped"] += 1
            return
        self.stats["stored"] += 1
        if self.on_ingested:
            self.on_ingested(result)

    def _store_with_retry(self, event):
        """Retry while the database is unavailable; give up only when shutting down."""
        delay = 1.0
        while True:
            try:
                with get_pool().connection() as conn:
                    return repository.ingest_event(conn, event)
            except psycopg.errors.ForeignKeyViolation as exc:
                log.warning("event %s refers to an unknown worker/station: %s", event["event_id"],
                            exc.diag.message_detail)
                return None
            except psycopg.OperationalError as exc:
                if self._stopping.is_set():
                    raise
                log.error("database unavailable (%s), retrying in %.0f s", exc, delay)
                time.sleep(delay)
                delay = min(delay * 2, 30)
