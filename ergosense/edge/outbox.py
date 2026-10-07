"""Durable outbox for events that the broker has not acknowledged yet (store-and-forward).

Every event is written here first. The publisher sends from the head of the queue in order
and deletes a row only after the broker's PUBACK (MQTT QoS 1). If the network drops, the rows
stay on disk - even if the edge process crashes - and are re-sent in order on reconnect.
Re-sending can create duplicates, which is fine: the backend ignores repeated event_ids.
"""
import sqlite3
import time


class Outbox:
    def __init__(self, path):
        self.conn = sqlite3.connect(str(path))
        self.conn.execute("PRAGMA journal_mode=WAL")      # readers don't block the writer
        self.conn.execute("PRAGMA synchronous=NORMAL")
        self.conn.execute(
            """CREATE TABLE IF NOT EXISTS outbox (
                   seq        INTEGER PRIMARY KEY AUTOINCREMENT,
                   event_id   TEXT UNIQUE NOT NULL,
                   topic      TEXT NOT NULL,
                   payload    BLOB NOT NULL,
                   created_at REAL NOT NULL)"""
        )
        self.conn.commit()

    def append(self, event_id, topic, payload):
        self.conn.execute(
            "INSERT OR IGNORE INTO outbox (event_id, topic, payload, created_at) VALUES (?, ?, ?, ?)",
            (event_id, topic, payload, time.time()),
        )
        self.conn.commit()

    def peek(self, after_seq=0, limit=50):
        """Oldest events with seq > after_seq, in order: [(seq, topic, payload), ...]."""
        return self.conn.execute(
            "SELECT seq, topic, payload FROM outbox WHERE seq > ? ORDER BY seq LIMIT ?",
            (after_seq, limit),
        ).fetchall()

    def remove(self, seq):
        self.conn.execute("DELETE FROM outbox WHERE seq = ?", (seq,))
        self.conn.commit()

    def __len__(self):
        return self.conn.execute("SELECT count(*) FROM outbox").fetchone()[0]

    def close(self):
        self.conn.close()
