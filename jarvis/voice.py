"""Speech in and out. STT: faster-whisper (local, free). TTS: edge-tts neural voice, macOS `say` fallback."""
import asyncio
import logging
import os
import platform
import queue
import re
import shutil
import subprocess
import tempfile
import threading
import time
from pathlib import Path

from . import config

log = logging.getLogger("jarvis.voice")
IS_MAC = platform.system() == "Darwin"


def clean_for_speech(text):
    text = re.sub(r"```.*?```", " (code shown on screen) ", text, flags=re.S)
    text = re.sub(r"\[(\d+)\]", "", text)
    text = re.sub(r"\[([^\]]+)\]\([^)]+\)", r"\1", text)
    text = re.sub(r"https?://\S+", "the link on screen", text)
    text = re.sub(r"[#*_`>|]+", "", text)
    text = re.sub(r"^\s*[-•]\s*", "", text, flags=re.M)
    return re.sub(r"\s+", " ", text).strip()


# ============================================================== TTS
def synth_to_file(text, out_path, voice=None, rate="+0%"):
    """Render speech to an audio file the browser can play. Returns the path actually written."""
    text = clean_for_speech(text)
    out_path = Path(out_path)
    try:
        import edge_tts

        async def _run():
            await edge_tts.Communicate(text, voice or config.TTS_VOICE, rate=rate).save(str(out_path.with_suffix(".mp3")))
        asyncio.run(_run())
        p = out_path.with_suffix(".mp3")
        if p.exists() and p.stat().st_size > 1000:
            return p
    except Exception as e:
        log.info("edge-tts unavailable (%s); using offline voice", e)
    if IS_MAC:
        aiff = out_path.with_suffix(".aiff")
        subprocess.run(["say", "-v", config.SAY_VOICE, "-o", str(aiff), text], check=True)
        m4a = out_path.with_suffix(".m4a")
        if shutil.which("ffmpeg"):
            subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-i", str(aiff), "-c:a", "aac", str(m4a)], check=True)
        else:
            subprocess.run(["afconvert", "-f", "m4af", "-d", "aac", str(aiff), str(m4a)], check=True)
        aiff.unlink(missing_ok=True)
        return m4a
    if shutil.which("espeak"):
        wav = out_path.with_suffix(".wav")
        subprocess.run(["espeak", "-w", str(wav), text], check=True)
        return wav
    raise RuntimeError("No text-to-speech engine available")


class Speaker:
    def __init__(self, enabled=True):
        self.enabled = enabled
        self.proc = None
        self.lock = threading.Lock()

    def say(self, text, block=True):
        if not self.enabled or not text.strip():
            return
        self.stop()
        tmp = Path(tempfile.mkdtemp()) / "speech"
        try:
            path = synth_to_file(text, tmp)
        except Exception as e:
            log.warning("TTS failed: %s", e)
            return
        player = ["afplay", str(path)] if IS_MAC else (["ffplay", "-nodisp", "-autoexit", "-loglevel", "quiet", str(path)]
                                                        if shutil.which("ffplay") else None)
        if not player:
            return
        with self.lock:
            self.proc = subprocess.Popen(player)
        if block:
            self.proc.wait()

    def stop(self):
        with self.lock:
            if self.proc and self.proc.poll() is None:
                self.proc.terminate()
            self.proc = None

    def speaking(self):
        return self.proc is not None and self.proc.poll() is None


# ============================================================== STT
_whisper = None


def whisper():
    global _whisper
    if _whisper is None:
        from faster_whisper import WhisperModel
        _whisper = WhisperModel(config.WHISPER_MODEL, device="cpu", compute_type="int8")
    return _whisper


def transcribe(audio):
    """audio: path to any audio file, or float32 numpy array at 16 kHz."""
    segments, _ = whisper().transcribe(audio, beam_size=1, vad_filter=True, language="en")
    return " ".join(s.text.strip() for s in segments).strip()


class Listener:
    """Records from the microphone until you stop talking (energy-based end-of-speech detection)."""
    RATE = 16000
    BLOCK = 1600  # 100 ms

    def __init__(self):
        import numpy as np
        import sounddevice as sd
        self.np, self.sd = np, sd
        self.noise = None
        self.on_level = None    # callback(level 0..1) while you speak — drives the HUD core
        self.on_speech = None   # callback() when speech starts

    def _calibrate(self, stream):
        levels = []
        for _ in range(8):
            data, _ = stream.read(self.BLOCK)
            levels.append(float(self.np.sqrt(self.np.mean(data ** 2))))
        self.noise = max(0.004, sorted(levels)[len(levels) // 2])

    def record(self, max_wait=8.0, max_len=30.0, silence=1.1):
        """Wait up to max_wait s for speech to start; stop after `silence` s of quiet. Returns np array or None."""
        np = self.np
        chunks, started, quiet_for, waited = [], False, 0.0, 0.0
        with self.sd.InputStream(samplerate=self.RATE, channels=1, dtype="float32", blocksize=self.BLOCK) as stream:
            if self.noise is None:
                self._calibrate(stream)
            thresh = self.noise * 3.2
            pre = []
            while True:
                data, _ = stream.read(self.BLOCK)
                data = data[:, 0].copy()
                lvl = float(np.sqrt(np.mean(data ** 2)))
                if not started:
                    pre = (pre + [data])[-4:]
                    waited += 0.1
                    if lvl > thresh:
                        started, chunks = True, pre[:]
                        if self.on_speech:
                            self.on_speech()
                    elif waited > max_wait:
                        # slowly adapt to room noise while idle
                        self.noise = 0.9 * self.noise + 0.1 * max(0.004, lvl)
                        return None
                    continue
                chunks.append(data)
                if self.on_level and len(chunks) % 2 == 0:
                    self.on_level(min(1.0, lvl / (thresh * 6)))
                quiet_for = quiet_for + 0.1 if lvl < thresh * 0.8 else 0.0
                if quiet_for >= silence or len(chunks) * 0.1 > max_len:
                    break
        audio = np.concatenate(chunks)
        return audio if len(audio) > self.RATE * 0.4 else None

    def listen(self, **kw):
        audio = self.record(**kw)
        if audio is None:
            return ""
        return transcribe(audio)


def strip_wake_word(text):
    """Return the command after the wake word, '' if only the wake word was said, None if not addressed."""
    low = text.lower()
    for w in config.WAKE_WORDS:
        m = re.search(rf"\b{w}\b[\s,.!?]*", low)
        if m:
            rest = text[m.end():].strip(" ,.!?")
            before = text[:m.start()].strip(" ,.!?")
            # "hey jarvis open safari" -> "open safari"; "open safari jarvis" -> "open safari"
            if not rest and before and before.lower() not in ("hey", "hi", "ok", "okay", "yo"):
                return before
            return rest
    return None
