"""Tiny persistent vector store (no heavy DB). Embeds with Ollama; falls back to keyword overlap."""
import json
import math
import re
import threading
import time
import uuid
from pathlib import Path

import requests

from . import config

_WORD = re.compile(r"[a-z0-9]+")


def _tokens(text):
    return set(w for w in _WORD.findall(text.lower()) if len(w) > 2)


def embed(text):
    """Return an embedding list, or None if the embed model is unavailable."""
    try:
        r = requests.post(f"{config.OLLAMA_URL}/api/embeddings",
                          json={"model": config.OLLAMA_EMBED_MODEL, "prompt": text[:4000]}, timeout=30)
        if r.ok:
            v = r.json().get("embedding")
            return v or None
    except Exception:
        pass
    return None


def _cos(a, b):
    dot = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a)) or 1e-9
    nb = math.sqrt(sum(y * y for y in b)) or 1e-9
    return dot / (na * nb)


class Store:
    def __init__(self, name):
        self.path = Path(config.DATA_DIR) / f"{name}.jsonl"
        self.lock = threading.Lock()
        self.items = []
        if self.path.exists():
            for line in self.path.read_text().splitlines():
                try:
                    self.items.append(json.loads(line))
                except Exception:
                    pass

    def add(self, text, meta=None, use_embedding=True):
        item = {"id": uuid.uuid4().hex[:12], "text": text, "meta": meta or {}, "ts": time.time(),
                "vec": embed(text) if use_embedding else None}
        with self.lock:
            self.items.append(item)
            with self.path.open("a") as f:
                f.write(json.dumps(item) + "\n")
        return item["id"]

    def search(self, query, k=4, where=None, min_score=0.0):
        cands = [i for i in self.items if not where or all(i["meta"].get(a) == b for a, b in where.items())]
        if not cands:
            return []
        qv = embed(query) if any(i.get("vec") for i in cands) else None
        qt = _tokens(query)
        scored = []
        for it in cands:
            if qv and it.get("vec") and len(it["vec"]) == len(qv):
                s = _cos(qv, it["vec"])
            else:
                t = _tokens(it["text"])
                s = len(qt & t) / (math.sqrt(len(qt) * len(t)) or 1)
            scored.append((s, it))
        scored.sort(key=lambda x: x[0], reverse=True)
        return [dict(it, score=s) for s, it in scored[:k] if s >= min_score]

    def all(self):
        return list(self.items)

    def rewrite(self, items):
        with self.lock:
            self.items = items
            self.path.write_text("".join(json.dumps(i) + "\n" for i in items))
