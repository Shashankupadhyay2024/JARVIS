"""Central configuration. Everything can be overridden in ~/.jarvis/.env"""
import os
from pathlib import Path

HOME = Path(os.environ.get("JARVIS_HOME", Path.home() / ".jarvis"))
ENV_FILE = HOME / ".env"


def _load_env():
    if ENV_FILE.exists():
        for line in ENV_FILE.read_text().splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, v = line.split("=", 1)
            os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))


_load_env()


def _get(name, default=""):
    return os.environ.get(name, default)


USER_NAME = _get("JARVIS_USER_NAME", "sir")

# ---- Models -------------------------------------------------------------
GEMINI_API_KEY = _get("GEMINI_API_KEY")
# Tried in order; the first one your key can use wins. Prefix match against the live model list.
GEMINI_MODELS = [m.strip() for m in _get(
    "GEMINI_MODELS",
    "gemini-3-flash,gemini-2.5-flash,gemini-3.1-flash-lite,gemini-2.5-flash-lite,gemini-2.0-flash",
).split(",") if m.strip()]
OLLAMA_URL = _get("OLLAMA_URL", "http://127.0.0.1:11434")
OLLAMA_MODEL = _get("OLLAMA_MODEL", "llama3.1:8b")
OLLAMA_VISION_MODEL = _get("OLLAMA_VISION_MODEL", "moondream")
OLLAMA_EMBED_MODEL = _get("OLLAMA_EMBED_MODEL", "nomic-embed-text")
OLLAMA_CTX = int(_get("OLLAMA_CTX", "8192"))

# ---- Voice --------------------------------------------------------------
TTS_VOICE = _get("JARVIS_TTS_VOICE", "en-GB-RyanNeural")      # edge-tts (free neural voice)
SAY_VOICE = _get("JARVIS_SAY_VOICE", "Daniel")                # macOS offline fallback
WHISPER_MODEL = _get("JARVIS_WHISPER_MODEL", "base.en")
WAKE_WORDS = [w.strip().lower() for w in _get("JARVIS_WAKE_WORDS", "jarvis,jarvus,travis,javis").split(",")]

# ---- Research -----------------------------------------------------------
MIN_CREDIBILITY = int(_get("JARVIS_MIN_CREDIBILITY", "80"))
CONTACT_EMAIL = _get("JARVIS_CONTACT_EMAIL", "")  # optional; OpenAlex "polite pool"

# ---- Paths --------------------------------------------------------------
OUTPUT_DIR = Path(_get("JARVIS_OUTPUT_DIR", str(Path.home() / "Documents" / "JARVIS")))
INBOX_DIR = OUTPUT_DIR / "inbox"
LECTURE_DIR = OUTPUT_DIR / "lectures"
DATA_DIR = HOME / "data"
CHROME_PROFILE = HOME / "chrome-profile"
LOG_FILE = HOME / "jarvis.log"
TEACHER_PORT = int(_get("JARVIS_TEACHER_PORT", "5057"))

for d in (HOME, OUTPUT_DIR, INBOX_DIR, LECTURE_DIR, DATA_DIR):
    d.mkdir(parents=True, exist_ok=True)
