"""Long-term memory about you and past conversations."""
from .store import Store

_mem = None


def mem():
    global _mem
    if _mem is None:
        _mem = Store("memory")
    return _mem


def remember(fact, kind="fact"):
    mem().add(fact.strip(), {"kind": kind})
    return f"Noted: {fact.strip()}"


def recall(query, k=5):
    hits = mem().search(query, k=k, min_score=0.3)
    return [h["text"] for h in hits]


def forget(query):
    items = mem().all()
    keep = [i for i in items if query.lower() not in i["text"].lower()]
    mem().rewrite(keep)
    return f"Forgot {len(items) - len(keep)} memory item(s) matching '{query}'."


def context_block(query):
    facts = [m["text"] for m in mem().all() if m["meta"].get("kind") == "fact"][-8:]
    rel = recall(query)
    seen, out = set(), []
    for f in facts + rel:
        if f not in seen:
            seen.add(f)
            out.append(f)
    if not out:
        return ""
    return "\n\n### What you remember about the user and past conversations:\n" + "\n".join(f"- {t}" for t in out)
