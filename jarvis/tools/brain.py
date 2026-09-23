"""Memory and self-improvement tools."""
import json
import os
import re
import subprocess

import requests

from .. import config, learning, memory, ui
from .registry import tool


@tool("Save a lasting fact about the user or their preferences (e.g. their major, schedule, how they like reports).",
      fact="the fact, in one sentence")
def remember(fact):
    return memory.remember(fact)


@tool("Search long-term memory for facts or past conversations.", query="what to look up")
def recall(query):
    return "\n".join(memory.recall(query, k=8)) or "Nothing relevant remembered."


@tool("Delete memories containing some text (when the user asks you to forget something).", text="text to match")
def forget(text):
    return memory.forget(text)


@tool("Store a lesson that improves future behaviour — use when the user corrects you, gives feedback, or when you "
      "discover a better way to do something.", lesson="a short, general rule, e.g. 'Always include DOIs in citations'")
def learn(lesson):
    return learning.add_lesson(lesson, "user")


@tool("Report what JARVIS has learned so far (examples collected, lessons, which model is active).")
def learning_status():
    s = learning.stats()
    lessons = [l["text"] for l in learning.lessons().all()][-10:]
    return (f"Gemini examples collected for the local model: {s['gemini_examples']}\nLessons: {s['lessons']}\n"
            f"Local model: {config.OLLAMA_MODEL}\nRecent lessons:\n" + "\n".join(f"- {l}" for l in lessons))


def _ollama_has(model):
    try:
        tags = requests.get(f"{config.OLLAMA_URL}/api/tags", timeout=5).json().get("models", [])
        return any(t["name"].split(":")[0] == model.split(":")[0] for t in tags)
    except Exception:
        return False


@tool("Evolve the local brain: bake the lessons learned and the best Gemini examples into a custom Ollama model "
      "called 'jarvis-brain' and switch to it. Run occasionally or when the user asks JARVIS to 'get smarter'.")
def evolve():
    base = os.environ.get("JARVIS_BASE_MODEL") or (config.OLLAMA_MODEL if config.OLLAMA_MODEL != "jarvis-brain" else "llama3.1:8b")
    lessons = [l["text"] for l in learning.lessons().all()][-40:]
    exs = [e for e in learning.examples().all() if e["meta"].get("task") in ("agent", "chat", "document", "teacher_answer")]
    # keep a diverse sample: newest per task
    picked, per_task = [], {}
    for e in reversed(exs):
        t = e["meta"]["task"]
        if per_task.get(t, 0) < 4 and len(e["meta"]["output"]) < 1800:
            per_task[t] = per_task.get(t, 0) + 1
            picked.append(e)
    clean = lambda s: s.replace('"""', "'''")
    system = (f"You are JARVIS, {config.USER_NAME}'s personal AI assistant and teacher. Think critically, verify before "
              "claiming, and be concise and warm.\nLessons learned:\n" + "\n".join(f"- {l}" for l in lessons))
    mf = [f"FROM {base}", f'SYSTEM """{clean(system)}"""', f"PARAMETER num_ctx {config.OLLAMA_CTX}", "PARAMETER temperature 0.5"]
    for e in reversed(picked):
        mf.append(f'MESSAGE user """{clean(e["text"][:1200])}"""')
        mf.append(f'MESSAGE assistant """{clean(e["meta"]["output"][:1800])}"""')
    path = config.HOME / "Modelfile"
    path.write_text("\n".join(mf))
    ui.status(f"Building jarvis-brain from {base} with {len(lessons)} lessons and {len(picked)} examples")
    p = subprocess.run(["ollama", "create", "jarvis-brain", "-f", str(path)], capture_output=True, text=True)
    if p.returncode != 0:
        return f"Evolve failed: {p.stderr[-500:]}"
    env = config.ENV_FILE.read_text() if config.ENV_FILE.exists() else ""
    env = re.sub(r"^OLLAMA_MODEL=.*\n?", "", env, flags=re.M)
    if "JARVIS_BASE_MODEL=" not in env:
        env += f"\nJARVIS_BASE_MODEL={base}\n"
    config.ENV_FILE.write_text(env.rstrip() + "\nOLLAMA_MODEL=jarvis-brain\n")
    config.OLLAMA_MODEL = "jarvis-brain"
    (config.DATA_DIR / "evolve.json").write_text(json.dumps({"examples_at_evolve": learning.stats()["gemini_examples"]}))
    return f"Evolved: local model is now 'jarvis-brain' ({len(lessons)} lessons, {len(picked)} worked examples baked in)."


def maybe_auto_evolve():
    """Re-bake the local model after every 60 new Gemini examples (runs quietly at startup)."""
    try:
        f = config.DATA_DIR / "evolve.json"
        last = json.loads(f.read_text())["examples_at_evolve"] if f.exists() else 0
        if learning.stats()["gemini_examples"] - last >= 60 and _ollama_has(os.environ.get("JARVIS_BASE_MODEL") or config.OLLAMA_MODEL):
            evolve()
    except Exception:
        pass
