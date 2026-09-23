"""LLM router: Gemini (free tier) first, local Ollama when Gemini is unavailable or out of quota."""
import base64
import json
import logging
import mimetypes
import re
import time
from pathlib import Path

import requests

from . import config, learning

log = logging.getLogger("jarvis.llm")


class LLMError(Exception):
    pass


class Router:
    def __init__(self):
        self.client = None
        self.gemini_models = []
        self.cooldown = {}          # model -> unix time when it may be retried
        self.last_provider = None
        self.force_local = False
        if config.GEMINI_API_KEY:
            try:
                from google import genai
                self.client = genai.Client(api_key=config.GEMINI_API_KEY)
            except Exception as e:
                log.warning("Gemini SDK unavailable: %s", e)

    # ---------------------------------------------------------------- Gemini
    def _discover(self):
        if self.gemini_models or not self.client:
            return self.gemini_models
        try:
            names = [m.name.split("/")[-1] for m in self.client.models.list()]
        except Exception as e:
            log.warning("Could not list Gemini models: %s", e)
            names = []
        chosen = []
        for pref in config.GEMINI_MODELS:
            matches = sorted([n for n in names if n.startswith(pref) and "image" not in n
                              and "tts" not in n and "audio" not in n], key=len)
            if matches and matches[0] not in chosen:
                chosen.append(matches[0])
        # If listing failed, just try the configured names directly.
        self.gemini_models = chosen or list(config.GEMINI_MODELS)
        return self.gemini_models

    def gemini_available(self):
        if not self.client or self.force_local:
            return False
        now = time.time()
        return any(self.cooldown.get(m, 0) < now for m in self._discover())

    def _gemini(self, messages, system, json_mode, images, temperature):
        from google.genai import types
        contents = []
        for m in messages:
            role = "model" if m["role"] == "assistant" else "user"
            contents.append(types.Content(role=role, parts=[types.Part.from_text(text=m["content"])]))
        if images:
            parts = contents[-1].parts
            for img in images:
                data, mime = _image_bytes(img)
                parts.append(types.Part.from_bytes(data=data, mime_type=mime))
        cfg = types.GenerateContentConfig(
            system_instruction=system or None, temperature=temperature,
            response_mime_type="application/json" if json_mode else None)
        last_err = None
        for model in self._discover():
            if self.cooldown.get(model, 0) > time.time():
                continue
            try:
                resp = self.client.models.generate_content(model=model, contents=contents, config=cfg)
                text = (resp.text or "").strip()
                if text:
                    self.last_provider = f"gemini:{model}"
                    return text
            except Exception as e:
                msg = str(e)
                last_err = e
                if "429" in msg or "RESOURCE_EXHAUSTED" in msg or "quota" in msg.lower():
                    daily = "per day" in msg.lower() or "perday" in msg.lower() or "daily" in msg.lower()
                    self.cooldown[model] = time.time() + (3600 if daily else 65)
                    log.info("Gemini %s rate-limited (%s); trying next", model, "daily" if daily else "per-minute")
                elif "404" in msg or "not found" in msg.lower():
                    self.cooldown[model] = time.time() + 86400
                else:
                    self.cooldown[model] = time.time() + 30
                    log.warning("Gemini %s error: %s", model, msg[:200])
        raise LLMError(f"All Gemini models unavailable: {last_err}")

    # ---------------------------------------------------------------- Ollama
    def _ollama(self, messages, system, json_mode, images, temperature, model=None):
        model = model or (config.OLLAMA_VISION_MODEL if images else config.OLLAMA_MODEL)
        msgs = ([{"role": "system", "content": system}] if system else []) + [dict(m) for m in messages]
        if images:
            msgs[-1]["images"] = [base64.b64encode(_image_bytes(i)[0]).decode() for i in images]
        body = {"model": model, "messages": msgs, "stream": False,
                "options": {"temperature": temperature, "num_ctx": config.OLLAMA_CTX}}
        if json_mode:
            body["format"] = "json"
        try:
            r = requests.post(f"{config.OLLAMA_URL}/api/chat", json=body, timeout=600)
        except requests.ConnectionError:
            raise LLMError("Ollama isn't running. Start it with:  ollama serve")
        if r.status_code == 404:
            raise LLMError(f"Ollama model '{model}' not installed. Run:  ollama pull {model}")
        r.raise_for_status()
        self.last_provider = f"ollama:{model}"
        return r.json()["message"]["content"].strip()

    # ---------------------------------------------------------------- public
    def chat(self, messages, system="", json_mode=False, images=None, task="chat",
             temperature=0.5, local_only=False):
        """messages: [{'role': 'user'|'assistant', 'content': str}]. Returns text."""
        prompt = messages[-1]["content"] if messages else ""
        if not local_only and self.gemini_available():
            try:
                out = self._gemini(messages, system, json_mode, images, temperature)
                if not images:
                    learning.record(task, system, prompt, out)
                return out
            except LLMError as e:
                log.info("Falling back to Ollama: %s", e)
        # Local path: add imitation examples + lessons so the small model performs better.
        boosted = system + learning.examples_block(task, prompt) + learning.lessons_block(prompt)
        return self._ollama(messages, boosted, json_mode, images, temperature)

    def ask(self, prompt, system="", **kw):
        return self.chat([{"role": "user", "content": prompt}], system=system, **kw)

    def ask_json(self, prompt, system="", **kw):
        text = self.ask(prompt, system=system, json_mode=True, **kw)
        try:
            return parse_json(text)
        except ValueError:
            fix = self.ask("Return ONLY valid JSON for this content, no commentary:\n" + text[:12000],
                           json_mode=True, task="json_fix")
            return parse_json(fix)


def parse_json(text):
    text = text.strip()
    text = re.sub(r"^```(?:json)?\s*|\s*```$", "", text)
    try:
        return json.loads(text)
    except Exception:
        pass
    for open_c, close_c in (("{", "}"), ("[", "]")):
        s, e = text.find(open_c), text.rfind(close_c)
        if s != -1 and e > s:
            try:
                return json.loads(text[s:e + 1])
            except Exception:
                continue
    raise ValueError("No JSON found in model output")


def _image_bytes(img):
    if isinstance(img, (bytes, bytearray)):
        return bytes(img), "image/png"
    p = Path(img)
    mime = mimetypes.guess_type(str(p))[0] or "image/png"
    return p.read_bytes(), mime


_router = None


def router():
    global _router
    if _router is None:
        _router = Router()
    return _router
