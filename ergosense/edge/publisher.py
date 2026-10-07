"""MQTT publisher that drains the SQLite outbox in order with QoS 1.

Why this design (Computer Networks part of the project):
  * QoS 1 = "at least once": the broker answers each PUBLISH with a PUBACK. We only delete an
    event from the outbox after its PUBACK, so a dropped connection can never lose an event.
  * At most `window` messages are in flight at once (a sliding window, like TCP), which keeps
    throughput high on a good link but bounds how much must be re-sent after a failure.
  * After a reconnect we restart from the head of the outbox, so events arrive in order.
    Some may arrive twice; the backend de-duplicates on event_id (idempotent ingest).
"""
import logging
import queue
import uuid

import paho.mqtt.client as mqtt

log = logging.getLogger("edge.publisher")


class EventPublisher:
    def __init__(self, host, port, outbox, client_id=None, window=20):
        self.outbox = outbox
        self.window = window
        self.connected = False
        self.sent = 0                  # PUBACKs received
        self._inflight = {}            # MQTT message id -> outbox seq
        self._last_sent_seq = 0
        self._acks = queue.Queue()     # filled by paho's network thread, read by pump()
        self._restart = False
        self._stopping = False

        self.client = mqtt.Client(
            callback_api_version=mqtt.CallbackAPIVersion.VERSION2,
            client_id=client_id or f"ergosense-edge-{uuid.uuid4().hex[:8]}",
            clean_session=True,
        )
        self.client.on_connect = self._on_connect
        self.client.on_disconnect = self._on_disconnect
        self.client.on_publish = self._on_publish
        self.client.reconnect_delay_set(min_delay=1, max_delay=10)
        self.host, self.port = host, port

    # paho callbacks run in paho's network thread -----------------------------
    def _on_connect(self, client, userdata, flags, reason_code, properties):
        if reason_code.is_failure:
            log.warning("broker refused connection: %s", reason_code)
            return
        log.info("connected to MQTT broker %s:%s", self.host, self.port)
        self.connected = True
        self._restart = True

    def _on_disconnect(self, client, userdata, flags, reason_code, properties):
        if self.connected and not self._stopping:
            log.warning("lost connection to broker (%s) - buffering events locally", reason_code)
        self.connected = False

    def _on_publish(self, client, userdata, mid, reason_code, properties):
        self._acks.put(mid)

    # called from the publisher process's main loop ---------------------------
    def start(self):
        # connect_async + loop_start: paho keeps retrying in the background if the broker is down
        self.client.connect_async(self.host, self.port, keepalive=15)
        self.client.loop_start()

    def stop(self):
        self._stopping = True
        self.client.loop_stop()
        self.client.disconnect()

    def pump(self):
        """Process acknowledgements, then send more events if the window has room."""
        while True:
            try:
                mid = self._acks.get_nowait()
            except queue.Empty:
                break
            seq = self._inflight.pop(mid, None)
            if seq is not None:
                self.outbox.remove(seq)
                self.sent += 1

        if not self.connected:
            return
        if self._restart:
            # (re)connected: anything not acknowledged is sent again from the oldest event
            self._inflight.clear()
            self._last_sent_seq = 0
            self._restart = False

        free = self.window - len(self._inflight)
        if free <= 0:
            return
        for seq, topic, payload in self.outbox.peek(self._last_sent_seq, free):
            info = self.client.publish(topic, payload, qos=1)
            if info.rc != mqtt.MQTT_ERR_SUCCESS:
                break
            self._inflight[info.mid] = seq
            self._last_sent_seq = seq

    @property
    def pending(self):
        return len(self.outbox)
