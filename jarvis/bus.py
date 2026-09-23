"""Tiny in-process event bus. The HUD subscribes; everything else just publishes."""
import queue
import threading
import time

_subs = []
_lock = threading.Lock()
_history = []
_state = {"state": "idle"}


def publish(kind, **data):
    ev = {"type": kind, "ts": time.time(), **data}
    if kind == "state":
        _state["state"] = data.get("state", "idle")
    with _lock:
        if kind not in ("level",):
            _history.append(ev)
            del _history[:-150]
        dead = []
        for q in _subs:
            try:
                q.put_nowait(ev)
            except queue.Full:
                dead.append(q)
        for q in dead:
            _subs.remove(q)
    return ev


def subscribe():
    q = queue.Queue(maxsize=2000)
    with _lock:
        _subs.append(q)
        backlog = list(_history)
    return q, backlog


def unsubscribe(q):
    with _lock:
        if q in _subs:
            _subs.remove(q)


def current_state():
    return _state["state"]
