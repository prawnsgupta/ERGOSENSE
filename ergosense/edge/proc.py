"""Small helpers shared by the edge processes."""
import logging
import queue
import signal


def child_init():
    """Run at the start of every child process.

    Children ignore Ctrl+C: the main process catches it and sets the shared stop_event, so
    every stage can shut down in order (capture -> scoring -> publisher) and flush its queue.
    """
    signal.signal(signal.SIGINT, signal.SIG_IGN)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)-15s %(message)s", datefmt="%H:%M:%S")


def put_drop_oldest(q, item):
    """Put into a bounded queue; if it is full, throw away the oldest item first.

    Used for live data (landmarks, display updates) where a stale item is worthless and the
    producer must never block. Events use a blocking put instead, because they must not be lost.
    """
    try:
        q.put_nowait(item)
    except queue.Full:
        try:
            q.get_nowait()
        except queue.Empty:
            pass
        try:
            q.put_nowait(item)
        except queue.Full:
            pass


def latest(q):
    """Drain a queue and return only the newest item (or None if it was empty)."""
    item = None
    while True:
        try:
            item = q.get_nowait()
        except queue.Empty:
            return item
