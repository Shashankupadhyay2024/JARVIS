"""JARVIS command line.

  jarvis                 talk ("Jarvis, ...") OR type — both work at the same time
  jarvis --ptt           press Enter, then speak (or type)
  jarvis --no-ui         don't open the holographic HUD window
  jarvis --text          typing only, mic off (replies still spoken; add --mute to silence)
  jarvis research "topic" [-n 8] [--min 80] [--slides]
  jarvis teach "topic" [--slides 8] [--file notes.pdf] [--video]
  jarvis analyze <file> ["question"]
  jarvis slides "topic" [--slides 10] [--file path]
  jarvis doctor          check that everything is installed
  jarvis evolve          bake what it has learned into the local model
"""
import argparse
import shutil
import sys
import time

from . import config, safety, ui, voice
from .llm import router

BANNER = r"""
     ██╗ █████╗ ██████╗ ██╗   ██╗██╗███████╗
     ██║██╔══██╗██╔══██╗██║   ██║██║██╔════╝
     ██║███████║██████╔╝██║   ██║██║███████╗
██   ██║██╔══██║██╔══██╗╚██╗ ██╔╝██║╚════██║
╚█████╔╝██║  ██║██║  ██║ ╚████╔╝ ██║███████║
 ╚════╝ ╚═╝  ╚═╝╚═╝  ╚═╝  ╚═══╝  ╚═╝╚══════╝"""
EXIT_WORDS = ("goodbye jarvis", "shut down jarvis", "exit", "quit", "goodbye", "go to sleep")


def show_reply(reply):
    c = ui.console
    prov = router().last_provider or ""
    if c:
        from rich.markdown import Markdown
        from rich.panel import Panel
        body = reply["say"] + ("\n\n" + reply["display"] if reply.get("display") else "")
        c.print(Panel(Markdown(body), title="[bold cyan]JARVIS[/]", subtitle=f"[dim]{prov}[/]", border_style="cyan"))
    else:
        print(f"\nJARVIS: {reply['say']}\n{reply.get('display', '')}")


def on_tool(name, args, thought):
    from . import bus
    bus.publish("tool", name=name, args={k: str(v)[:200] for k, v in args.items()}, thought=thought)
    arg_s = ", ".join(f"{k}={str(v)[:60]!r}" for k, v in args.items())
    (ui.console.print if ui.console else print)(f"{name}({arg_s})" + (f"  — {thought[:90]}" if thought else ""))


HELP = """Type any message, or speak. Commands you can type:
  /mute  /unmute      stop or resume spoken replies
  /mic off  /mic on   stop or resume listening to the microphone
  (Enter while JARVIS is talking silences it)   exit  quits"""


def run_agent_loop(mode, mute, show_ui=True, app_mode=False):
    """Voice and keyboard work at the same time: speak ("Jarvis, ...") or just type and press Enter."""
    import queue
    import threading
    from .agent import Agent
    from .teacher import server as teacher_server

    agent = Agent()
    speaker = voice.Speaker(enabled=not mute)
    ui.speaker = speaker
    inbox = queue.Queue()          # ("text"|"voice"|"ptt"|"quit", message)
    busy = threading.Event()       # set while JARVIS thinks/speaks, so the mic ignores its own voice
    mic_on = threading.Event()
    state = {"follow_up_until": 0.0, "awaiting": False}
    from . import bus
    from .hud import server as hud

    def sync_flags():
        hud.set_flags(mic=mic_on.is_set(), mute=not speaker.enabled, mode=mode)

    def set_state(s):
        bus.publish("state", state=s)

    try:
        url = hud.start(inbox, open_window=show_ui)
        ui.status(f"HUD online: {url.split('#')[0]}" + ("" if show_ui else "  (run without --no-ui to open it)"))
    except Exception as e:
        ui.status(f"HUD unavailable ({e}); terminal only.")

    listener = None
    if mode in ("wake", "ptt"):
        try:
            listener = voice.Listener()
            listener.on_level = lambda v: bus.publish("level", value=v)
            listener.on_speech = lambda: set_state("listening")
            ui.status("Loading speech recognition…")
            voice.whisper()
        except Exception as e:
            ui.status(f"Microphone/speech recognition unavailable ({e}). You can still type.")
            mode = "text"

    def prompt():
        print("\nYou › ", end="", flush=True)

    def keyboard():
        while True:
            try:
                line = input()
            except (EOFError, KeyboardInterrupt):
                inbox.put(("quit", ""))
                return
            line = line.strip()
            if not line and mode == "ptt" and not busy.is_set():
                inbox.put(("ptt", ""))
            else:
                inbox.put(("text", line))

    def microphone():
        while True:
            if not mic_on.is_set() or (busy.is_set() and not state["awaiting"]) or teacher_server.is_playing():
                time.sleep(0.2)
                continue
            try:
                heard = listener.listen(max_wait=4)
            except Exception as e:
                ui.status(f"Microphone error ({e}); listening paused. Keep typing, or /mic on to retry.")
                mic_on.clear()
                continue
            if not heard or (busy.is_set() and not state["awaiting"]):
                if not busy.is_set():
                    set_state("idle")
                continue
            if state["awaiting"]:
                inbox.put(("voice", heard))
                continue
            cmd = voice.strip_wake_word(heard)
            if cmd is None:
                if time.time() < state["follow_up_until"]:
                    inbox.put(("voice", heard))   # follow-up right after a reply: no wake word needed
                else:
                    set_state("idle")
                continue
            inbox.put(("voice", cmd))             # "" means only the wake word was said

    def confirm(question):
        """Risky actions: accept a typed y/yes or a spoken 'confirm'."""
        if ui.console:
            ui.console.print(f"\n[bold yellow]⚠ Needs your OK:[/] {question}")
        else:
            print(f"\n⚠ Needs your OK: {question}")
        print("Type y to allow" + (" (or say “confirm”)" if mic_on.is_set() else "") + ", anything else cancels › ",
              end="", flush=True)
        speaker.say("That action needs your permission." + (" Say confirm, or type y." if mic_on.is_set() else " Type y to allow."))
        bus.publish("confirm", question=question)
        set_state("confirm")
        state["awaiting"] = True
        try:
            kind, ans = inbox.get(timeout=90)
        except queue.Empty:
            ui.status("No answer; cancelled.")
            return False
        finally:
            state["awaiting"] = False
            bus.publish("confirm_done")
            set_state("thinking")
        ans = ans.lower().strip(" .!")
        ui.status("Authorized." if ans in ("y", "yes", "confirm") else f"Answer: {ans!r}")
        if kind == "voice":
            ui.status(f"heard: {ans!r}")
            return any(w in ans for w in ("confirm", "yes", "go ahead", "do it", "proceed"))
        return ans in ("y", "yes", "confirm")

    safety.confirmer.ask = confirm

    print(BANNER)
    llm = router()
    brain = "Gemini + local Ollama backup" if llm.gemini_available() else f"local {config.OLLAMA_MODEL} (no Gemini key)"
    hint = {"wake": 'Say "Jarvis …" or just type. /help for commands.',
            "ptt": "Press Enter then speak, or type a message. /help for commands.",
            "text": "Type a message (drag a file in to paste its path). /help for commands."}[mode]
    ui.status(f"Brain: {brain}. {hint}")
    if not app_mode:
        threading.Thread(target=keyboard, daemon=True).start()
    else:
        def watch_window():
            """App mode: closing the HUD window shuts JARVIS down (after a grace period for reloads)."""
            start = time.time()
            while True:
                time.sleep(3)
                if hud.window_closed_for(20) or (not hud._clients["ever"] and time.time() - start > 180):
                    ui.status("HUD window closed — shutting down.")
                    inbox.put(("quit", ""))
                    return
        threading.Thread(target=watch_window, daemon=True).start()
    if listener and mode == "wake":
        mic_on.set()
        threading.Thread(target=microphone, daemon=True).start()
    sync_flags()

    def speak_interruptibly(text):
        """Speak without blocking input: pressing Enter (or typing stop) silences JARVIS; other typing is kept."""
        if speaker.enabled and text.strip():
            set_state("speaking")
        speaker.say(text, block=False)
        held = []
        while speaker.speaking():
            try:
                item = inbox.get(timeout=0.1)
            except queue.Empty:
                continue
            if item[0] in ("text", "ptt") and item[1].lower() in ("", "stop", "quiet", "shh", "stop talking"):
                speaker.stop()
            else:
                held.append(item)
        for item in held:
            inbox.put(item)
        set_state("idle")

    hour = time.localtime().tm_hour
    greet = "Good morning" if hour < 12 else "Good afternoon" if hour < 18 else "Good evening"
    busy.set()
    greeting = f"{greet}, {config.USER_NAME}. JARVIS online. How can I help?"
    bus.publish("reply", say=greeting, display="", provider="")
    speak_interruptibly(greeting)
    busy.clear()
    prompt()

    while True:
        try:
            kind, msg = inbox.get()
            if kind == "quit":
                speaker.stop()
                bus.publish("state", state="offline")
                print("\nGoodbye.")
                if app_mode:
                    speaker.say("Powering down. Goodbye.")
                return
            if kind == "ptt":
                busy.set()
                ui.status("listening…")
                msg = listener.listen(max_wait=6) if listener else ""
                busy.clear()
                if not msg:
                    ui.status("didn't catch that")
                    prompt()
                    continue
                kind = "voice"
            if kind == "voice":
                if msg == "":
                    set_state("listening")
                    busy.set()
                    speak_interruptibly("Yes?")
                    busy.clear()
                    state["follow_up_until"] = time.time() + 8
                    continue
                print()
                ui.status(f"heard: {msg}")
            if kind == "confirm":   # a stray authorize/deny click with nothing pending
                continue
            if not msg:
                prompt()
                continue
            low = msg.lower().strip(" .!?")
            if low in ("/help", "help me use this", "/?"):
                print(HELP)
            elif low in ("/mute", "mute"):
                speaker.enabled = False
                speaker.stop()
                ui.status("Spoken replies off (/unmute to turn back on).")
                sync_flags()
            elif low in ("/unmute", "unmute"):
                speaker.enabled = True
                ui.status("Spoken replies on.")
                sync_flags()
            elif low in ("/mic off", "mic off"):
                mic_on.clear()
                ui.status("Microphone off — typing only (/mic on to resume).")
                sync_flags()
            elif low in ("/mic on", "mic on"):
                if listener is None:
                    try:
                        listener = voice.Listener()
                        voice.whisper()
                    except Exception as e:
                        ui.status(f"Microphone unavailable: {e}")
                if listener:
                    first = not mic_on.is_set() and mode != "wake"
                    mic_on.set()
                    if first:
                        mode = "wake"
                        threading.Thread(target=microphone, daemon=True).start()
                    ui.status('Listening. Say "Jarvis …".')
                sync_flags()
            elif low in EXIT_WORDS:
                speaker.say("Goodbye. I'll be here when you need me.")
                return
            elif low in ("stop", "quiet", "shut up", "be quiet", "cancel"):
                speaker.stop()
                set_state("idle")
            else:
                busy.set()
                bus.publish("user", text=msg, via="voice" if kind == "voice" else "text")
                set_state("thinking")
                try:
                    reply = agent.handle(msg, on_tool=on_tool)
                    show_reply(reply)
                    bus.publish("reply", say=reply["say"], display=reply.get("display", ""),
                                provider=router().last_provider or "")
                    speak_interruptibly(reply["say"])
                finally:
                    busy.clear()
                    set_state("idle")
                    state["follow_up_until"] = time.time() + 8
            prompt()
        except KeyboardInterrupt:
            speaker.stop()
            print("\nGoodbye.")
            return
        except Exception as e:
            busy.clear()
            set_state("idle")
            ui.status(f"Error: {e}")
            speaker.say("Sorry, something went wrong on my side. The details are in the log.")
            prompt()


def doctor():
    import requests
    ok = lambda b: "✅" if b else "❌"
    print("JARVIS doctor\n")
    r = router()
    print(f"{ok(bool(config.GEMINI_API_KEY))} Gemini API key set")
    if config.GEMINI_API_KEY:
        models = r._discover()
        print(f"   Gemini models to use: {models}")
        try:
            print(f"   test: {r.ask('Reply with the single word: ready', task='doctor')[:40]!r} via {r.last_provider}")
        except Exception as e:
            print(f"   ❌ Gemini test failed: {e}")
    try:
        tags = [t["name"] for t in requests.get(f"{config.OLLAMA_URL}/api/tags", timeout=5).json()["models"]]
        print(f"✅ Ollama running; models: {tags}")
        for m in (config.OLLAMA_MODEL, config.OLLAMA_EMBED_MODEL, config.OLLAMA_VISION_MODEL):
            have = any(t.split(":")[0] == m.split(":")[0] for t in tags)
            print(f"   {ok(have)} {m}" + ("" if have else f"   → run: ollama pull {m}"))
    except Exception:
        print("❌ Ollama not reachable → run: brew services start ollama")
    print(f"{ok(shutil.which('ffmpeg'))} ffmpeg (video export)")
    import os
    chrome = os.path.exists("/Applications/Google Chrome.app")
    print(f"{ok(chrome)} Google Chrome (Google Scholar research)")
    for mod, why in (("faster_whisper", "speech recognition"), ("sounddevice", "microphone"), ("edge_tts", "neural voice"),
                     ("selenium", "Google Scholar"), ("ddgs", "web search"), ("pptx", "slides"), ("docx", "Word docs")):
        try:
            __import__(mod)
            print(f"✅ {mod} ({why})")
        except Exception as e:
            print(f"❌ {mod} ({why}): {e}")
    try:
        import sounddevice as sd
        dev = sd.query_devices(kind="input")
        print(f"✅ microphone: {dev['name']}")
    except Exception as e:
        print(f"❌ microphone: {e}  (System Settings → Privacy & Security → Microphone → enable Terminal)")
    print(f"\nOutputs go to: {config.OUTPUT_DIR}\nDrop files to analyse in: {config.INBOX_DIR}\nSettings: {config.ENV_FILE}")


def main(argv=None):
    ap = argparse.ArgumentParser(prog="jarvis", description="JARVIS personal assistant & teacher")
    ap.add_argument("--text", action="store_true", help="type instead of talk")
    ap.add_argument("--ptt", action="store_true", help="push-to-talk (press Enter, then speak)")
    ap.add_argument("--mute", action="store_true", help="don't speak replies")
    ap.add_argument("--local", action="store_true", help="use only the local Ollama model")
    ap.add_argument("--no-ui", action="store_true", help="don't open the HUD window")
    ap.add_argument("--app", action="store_true", help=argparse.SUPPRESS)  # launched from JARVIS.app (no terminal)
    sub = ap.add_subparsers(dest="cmd")
    r = sub.add_parser("research"); r.add_argument("topic"); r.add_argument("-n", type=int, default=8)
    r.add_argument("--min", type=int, default=config.MIN_CREDIBILITY); r.add_argument("--slides", action="store_true")
    r.add_argument("--style", default="APA")
    t = sub.add_parser("teach"); t.add_argument("topic"); t.add_argument("--slides", type=int, default=8)
    t.add_argument("--file", default=""); t.add_argument("--video", action="store_true")
    a = sub.add_parser("analyze"); a.add_argument("path"); a.add_argument("question", nargs="?", default="")
    s = sub.add_parser("slides"); s.add_argument("topic"); s.add_argument("--slides", type=int, default=10); s.add_argument("--file", default="")
    sub.add_parser("doctor"); sub.add_parser("evolve")
    args = ap.parse_args(argv)
    if args.local:
        router().force_local = True

    if args.cmd == "doctor":
        return doctor()
    if args.cmd:
        from .agent import registry  # registers tools
        if not args.mute:
            ui.speaker = voice.Speaker()
        if args.cmd == "research":
            out = registry.call("research", {"topic": args.topic, "count": args.n, "min_credibility": args.min,
                                              "make_slides": args.slides, "citation_style": args.style})
            print(out)
            path = next((l.split(": ", 1)[1] for l in out.splitlines() if l.startswith("Report saved")), None)
            if path:
                import subprocess
                subprocess.run(["open", path])
        elif args.cmd == "teach":
            print(registry.call("teach", {"topic": args.topic, "slides": args.slides, "source_file": args.file, "video": args.video}))
            print("\nClassroom running. Press Ctrl+C here when you're finished.")
            try:
                while True:
                    time.sleep(1)
            except KeyboardInterrupt:
                pass
        elif args.cmd == "analyze":
            kw = {"path": args.path}
            if args.question:
                kw["question"] = args.question
            print(registry.call("analyze_file", kw))
        elif args.cmd == "slides":
            print(registry.call("make_slides", {"topic": args.topic, "slides": args.slides, "source_file": args.file}))
        elif args.cmd == "evolve":
            print(registry.call("evolve", {}))
        return
    mode = "text" if args.text else "ptt" if args.ptt else "wake"
    run_agent_loop(mode, args.mute, show_ui=not args.no_ui, app_mode=args.app)


if __name__ == "__main__":
    sys.exit(main())
