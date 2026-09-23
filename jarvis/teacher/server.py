"""Local web app that presents a lecture and lets you raise your hand and ask questions mid-lesson."""
import json
import logging
import tempfile
import threading
import time
import uuid
import webbrowser
from pathlib import Path

from flask import Flask, jsonify, request, send_from_directory

from .. import config
from ..llm import router

log = logging.getLogger("jarvis.teacher")
STATIC = Path(__file__).parent / "static"
app = Flask(__name__, static_folder=None)
state = {"folder": None, "spec": None, "qa": [], "playing_at": 0.0, "video": {"status": "idle"}}
_server_thread = None


def is_playing():
    return time.time() - state["playing_at"] < 8


@app.get("/")
def index():
    return send_from_directory(STATIC, "index.html")


@app.get("/api/lecture")
def lecture():
    return jsonify(state["spec"] or {})


@app.get("/media/<path:p>")
def media(p):
    return send_from_directory(state["folder"], p)


@app.post("/api/heartbeat")
def heartbeat():
    if (request.get_json(silent=True) or {}).get("playing"):
        state["playing_at"] = time.time()
    return jsonify(ok=True)


def teacher_answer(question, slide_idx):
    spec = state["spec"]
    slides = spec["slides"]
    cur = slides[slide_idx - 1] if 1 <= slide_idx <= len(slides) else None
    outline = "\n".join(f"{i}. {s['title']}" for i, s in enumerate(slides, 1))
    ctx = (f"Current slide {slide_idx}: {cur['title']}\nBullets: {cur['bullets']}\nWhat you were saying: {cur.get('notes', '')}"
           if cur else "You are on the title/closing slide.")
    history = "\n".join(f"Student: {q['question']}\nYou: {q['answer']}" for q in state["qa"][-4:])
    data = router().ask_json(
        f"Lecture: {spec['title']}\nOutline:\n{outline}\n\n{ctx}\n\nEarlier questions this lesson:\n{history or '(none)'}\n\n"
        f"The student raised their hand and asked: \"{question}\"\n\n"
        "Answer like an excellent professor speaking aloud: direct answer first, then a concrete example or analogy, "
        "then connect it back to the slide. 60-150 words, no markdown, no lists in the spoken part. If it is covered "
        "later in the outline, answer briefly and say we'll dig in soon. If the question reveals a misconception, gently "
        "correct it. If you are unsure of a fact, say so. End with a short check like 'Does that make sense?'.\n"
        "Return JSON: {\"spoken\": str, \"board\": [up to 4 short whiteboard notes, formulas or key terms]}",
        system="You are JARVIS, a warm, sharp and patient teacher.", task="teacher_answer", temperature=0.4)
    return data.get("spoken", "").strip(), [str(b) for b in data.get("board", [])][:4]


@app.post("/api/ask")
def ask():
    from ..voice import synth_to_file, transcribe
    slide_idx = int(request.form.get("slide", 0))
    question = (request.form.get("text") or "").strip()
    if not question and "audio" in request.files:
        tmp = Path(tempfile.mkdtemp()) / "q.webm"
        request.files["audio"].save(tmp)
        try:
            question = transcribe(str(tmp))
        except Exception as e:
            return jsonify(error=f"Couldn't transcribe: {e}"), 500
    if not question:
        return jsonify(error="I didn't catch a question — try again or type it."), 400
    try:
        spoken, board = teacher_answer(question, slide_idx)
    except Exception as e:
        return jsonify(error=f"Thinking failed: {e}"), 500
    qa_dir = Path(state["folder"]) / "qa"
    qa_dir.mkdir(exist_ok=True)
    audio = synth_to_file(spoken, qa_dir / f"a_{uuid.uuid4().hex[:8]}")
    item = {"question": question, "answer": spoken, "board": board, "slide": slide_idx,
            "audio": str(Path(audio).relative_to(state["folder"]))}
    state["qa"].append(item)
    (Path(state["folder"]) / "questions.json").write_text(json.dumps(state["qa"], indent=2))
    return jsonify(item)


@app.post("/api/video")
def video():
    if state["video"]["status"] == "working":
        return jsonify(state["video"])

    def work():
        from .video import export_video
        try:
            out = export_video(state["folder"])
            state["video"] = {"status": "done", "path": str(out)}
        except Exception as e:
            state["video"] = {"status": "error", "error": str(e)}
    state["video"] = {"status": "working"}
    threading.Thread(target=work, daemon=True).start()
    return jsonify(state["video"])


@app.get("/api/video")
def video_status():
    return jsonify(state["video"])


@app.post("/api/reveal")
def reveal():
    import subprocess
    p = state["video"].get("path")
    if p:
        subprocess.run(["open", "-R", p])
    return jsonify(ok=True)


def load(folder):
    folder = Path(folder)
    state.update(folder=str(folder), spec=json.loads((folder / "lecture.json").read_text()), qa=[],
                 video={"status": "idle"})


def serve(folder, open_browser=True):
    """Start (or retarget) the lecture server in the background and open it in the browser."""
    global _server_thread
    load(folder)
    if _server_thread is None or not _server_thread.is_alive():
        logging.getLogger("werkzeug").setLevel(logging.ERROR)
        _server_thread = threading.Thread(
            target=lambda: app.run(host="127.0.0.1", port=config.TEACHER_PORT, debug=False, use_reloader=False, threaded=True),
            daemon=True)
        _server_thread.start()
        time.sleep(1.0)
    url = f"http://127.0.0.1:{config.TEACHER_PORT}/?t={int(time.time())}"
    if open_browser:
        webbrowser.open(url)
    return url
