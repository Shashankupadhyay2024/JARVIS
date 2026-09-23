"""The JARVIS HUD: a local web interface that mirrors and drives the assistant loop.

Everything is bound to 127.0.0.1 and every POST needs a per-session key, so other websites
open in your browser can't send commands to JARVIS.
"""
import json
import logging
import os
import platform
import secrets
import subprocess
import tempfile
import threading
import time
import webbrowser
from pathlib import Path

from flask import Flask, Response, abort, jsonify, request, send_from_directory

from .. import bus, config, learning

STATIC = Path(__file__).parent / "static"
KEY = secrets.token_urlsafe(18)
PORT = int(os.environ.get("JARVIS_HUD_PORT", "5056"))
app = Flask(__name__, static_folder=None)
_inbox = None
_flags = {"mic": False, "mute": False, "mode": "text"}
_thread = None
_clients = {"n": 0, "ever": False, "last_change": time.time()}
KEY_FILE = config.HOME / "hud.key"
OPENABLE = (".docx", ".pptx", ".pdf", ".mp4", ".png", ".jpg", ".json", ".txt", ".md", ".xlsx", ".csv", ".html")


def _auth():
    if request.headers.get("X-Jarvis-Key") != KEY:
        abort(403)


@app.get("/")
def index():
    return send_from_directory(STATIC, "index.html")


@app.get("/events")
def events():
    if request.args.get("k") != KEY:
        abort(403)
    q, backlog = bus.subscribe()
    _clients["n"] += 1
    _clients["ever"] = True
    _clients["last_change"] = time.time()

    def stream():
        try:
            yield f"data: {json.dumps({'type': 'hello', 'flags': _flags, 'state': bus.current_state(), 'user': config.USER_NAME})}\n\n"
            for ev in backlog:
                yield f"data: {json.dumps(ev, default=str)}\n\n"
            while True:
                try:
                    ev = q.get(timeout=5)
                    yield f"data: {json.dumps(ev, default=str)}\n\n"
                except Exception:
                    yield ": keepalive\n\n"
        finally:
            bus.unsubscribe(q)
            _clients["n"] -= 1
            _clients["last_change"] = time.time()
    return Response(stream(), mimetype="text/event-stream", headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


@app.post("/api/message")
def message():
    _auth()
    text = ((request.get_json(silent=True) or {}).get("text") or "").strip()
    if text:
        _inbox.put(("text", text))
    return jsonify(ok=True)


@app.post("/api/voice")
def hud_voice():
    """Push-to-talk from the HUD: browser records, we transcribe locally."""
    _auth()
    from ..voice import transcribe
    f = request.files.get("audio")
    if not f:
        return jsonify(error="no audio"), 400
    tmp = Path(tempfile.mkdtemp()) / "hud.webm"
    f.save(tmp)
    bus.publish("state", state="thinking")
    try:
        text = transcribe(str(tmp))
    except Exception as e:
        bus.publish("state", state="idle")
        return jsonify(error=str(e)), 500
    if text:
        _inbox.put(("voice", text))
    else:
        bus.publish("state", state="idle")
        bus.publish("activity", text="Didn't catch that — try again or type.")
    return jsonify(text=text)


@app.post("/api/confirm")
def confirm():
    _auth()
    allow = bool((request.get_json(silent=True) or {}).get("allow"))
    _inbox.put(("confirm", "y" if allow else "n"))
    return jsonify(ok=True)


@app.post("/api/command")
def command():
    _auth()
    cmd = (request.get_json(silent=True) or {}).get("cmd", "")
    if cmd in ("/mute", "/unmute", "/mic on", "/mic off", "stop"):
        _inbox.put(("text", cmd))
    elif cmd == "exit":
        _inbox.put(("quit", ""))
    return jsonify(ok=True)


@app.post("/api/upload")
def upload():
    _auth()
    saved = []
    for f in request.files.getlist("files"):
        name = Path(f.filename or "file").name
        dest = config.INBOX_DIR / name
        stem, n = dest.stem, 1
        while dest.exists():
            dest = config.INBOX_DIR / f"{stem}_{n}{dest.suffix}"
            n += 1
        f.save(dest)
        saved.append(str(dest))
    bus.publish("activity", text=f"Received {len(saved)} file(s) into the inbox")
    return jsonify(paths=saved)


@app.get("/api/status")
def status():
    info = {"time": time.time(), "brain": "", "learning": learning.stats(), "flags": _flags}
    try:
        from ..llm import router
        r = router()
        info["brain"] = r.last_provider or ("gemini" if r.gemini_available() else f"ollama:{config.OLLAMA_MODEL}")
        info["gemini_ready"] = r.gemini_available()
    except Exception:
        pass
    try:
        import psutil
        info["cpu"] = psutil.cpu_percent(interval=None)
        info["mem"] = psutil.virtual_memory().percent
        info["disk"] = psutil.disk_usage(str(Path.home())).percent
        b = psutil.sensors_battery()
        if b:
            info["battery"] = round(b.percent)
            info["plugged"] = bool(b.power_plugged)
    except Exception:
        pass
    return jsonify(info)


@app.get("/api/files")
def files():
    items = []
    for p in config.OUTPUT_DIR.rglob("*"):
        if p.is_file() and p.suffix.lower() in OPENABLE and "video_parts" not in p.parts and "audio" not in p.parts \
                and "images" not in p.parts and "qa" not in p.parts and p.name not in ("lecture.json", "spec.json"):
            items.append(p)
    items.sort(key=lambda p: p.stat().st_mtime, reverse=True)
    return jsonify([{"name": p.name, "path": str(p), "mtime": p.stat().st_mtime, "ext": p.suffix.lower()[1:]} for p in items[:14]])


@app.post("/api/open")
def open_file():
    _auth()
    p = Path((request.get_json(silent=True) or {}).get("path", "")).resolve()
    if config.OUTPUT_DIR.resolve() not in p.parents or not p.exists():
        abort(400)
    subprocess.run(["open", str(p)] if platform.system() == "Darwin" else ["xdg-open", str(p)])
    return jsonify(ok=True)


def set_flags(**kw):
    _flags.update(kw)
    bus.publish("flags", flags=dict(_flags))


def start(inbox, open_window=True):
    """Start the HUD in the background and (optionally) open it in an app-style window."""
    global _inbox, _thread
    _inbox = inbox
    if _thread is None:
        logging.getLogger("werkzeug").setLevel(logging.ERROR)
        _thread = threading.Thread(target=lambda: app.run(host="127.0.0.1", port=PORT, threaded=True,
                                                          debug=False, use_reloader=False), daemon=True)
        _thread.start()
        time.sleep(0.8)
    url = f"http://127.0.0.1:{PORT}/#k={KEY}"
    KEY_FILE.write_text(KEY)
    KEY_FILE.chmod(0o600)
    if open_window:
        open_window_at(url)
    return url


def window_closed_for(seconds):
    """True once the HUD has been opened at least once and no window has been connected for `seconds`."""
    return _clients["ever"] and _clients["n"] <= 0 and time.time() - _clients["last_change"] > seconds


def open_window_at(url):
    chrome = "/Applications/Google Chrome.app"
    if True:
        if platform.system() == "Darwin" and os.path.exists(chrome):
            subprocess.Popen(["open", "-na", "Google Chrome", "--args", f"--app={url}", "--window-size=1440,900",
                              f"--user-data-dir={config.HOME / 'hud-profile'}", "--autoplay-policy=no-user-gesture-required"])
        else:
            webbrowser.open(url)
