"""How JARVIS gets smarter over time.

1. Imitation: every good Gemini answer is saved. When Gemini is unavailable, the most similar
   saved Gemini answers are shown to Ollama as worked examples ("answer like this").
2. Lessons: short rules learned from your corrections ("jarvis, learn that ...") and from
   JARVIS's own reflections after mistakes. Relevant lessons are injected into every prompt.
3. A clean training file (~/.jarvis/data/finetune.jsonl) accumulates, so you can fine-tune a
   local model on Gemini's behaviour later (see README).
"""
import json
import time

from . import config
from .store import Store

_examples = None
_lessons = None
FINETUNE_FILE = config.DATA_DIR / "finetune.jsonl"


def examples():
    global _examples
    if _examples is None:
        _examples = Store("gemini_examples")
    return _examples


def lessons():
    global _lessons
    if _lessons is None:
        _lessons = Store("lessons")
    return _lessons


def record(task, system, prompt, output):
    """Save a Gemini answer so the local model can learn from it."""
    if not output or len(output) < 20:
        return
    examples().add(prompt[:1500], {"task": task, "output": output[:3500]})
    with FINETUNE_FILE.open("a") as f:
        msgs = ([{"role": "system", "content": system[:4000]}] if system else []) + [
            {"role": "user", "content": prompt[:8000]}, {"role": "assistant", "content": output[:8000]}]
        f.write(json.dumps({"task": task, "ts": time.time(), "messages": msgs}) + "\n")


def examples_block(task, prompt, k=2):
    hits = examples().search(prompt, k=k, where={"task": task}, min_score=0.35)
    if not hits:
        return ""
    parts = ["\n\n### Reference answers written by a stronger model for similar requests. "
             "Match their quality, structure and depth (do not copy facts that don't apply):"]
    for i, h in enumerate(hits, 1):
        parts.append(f"\n[Example {i}] Request: {h['text'][:600]}\nAnswer: {h['meta']['output'][:1500]}")
    return "\n".join(parts)


def add_lesson(text, source="user"):
    lessons().add(text.strip(), {"source": source})
    return f"Lesson stored: {text.strip()}"


def lessons_block(query, k=6):
    hits = lessons().search(query, k=k, min_score=0.2) if lessons().all() else []
    # user-given lessons always matter more; include the newest few regardless of similarity
    user_recent = [l for l in lessons().all() if l["meta"].get("source") == "user"][-3:]
    seen, out = set(), []
    for h in user_recent + hits:
        if h["text"] not in seen:
            seen.add(h["text"])
            out.append(h["text"])
    if not out:
        return ""
    return "\n\n### Lessons learned from past experience (follow these):\n" + "\n".join(f"- {t}" for t in out)


def stats():
    return {"gemini_examples": len(examples().all()), "lessons": len(lessons().all())}
