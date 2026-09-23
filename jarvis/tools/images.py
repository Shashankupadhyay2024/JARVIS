"""Image generation: Gemini image model if your key allows it, otherwise Pollinations.ai (free, no key)."""
import logging
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from urllib.parse import quote

import requests

from .. import config
from ..llm import router
from .registry import tool

log = logging.getLogger("jarvis.images")
_gemini_image_ok = True


def _gemini_image(prompt, out):
    global _gemini_image_ok
    r = router()
    if not _gemini_image_ok or not r.client:
        return None
    try:
        from google.genai import types
        models = [m.name.split("/")[-1] for m in r.client.models.list()]
        cand = [m for m in models if "image" in m and ("flash" in m or "imagen" in m)]
        for m in cand:
            try:
                resp = r.client.models.generate_content(
                    model=m, contents=prompt, config=types.GenerateContentConfig(response_modalities=["IMAGE", "TEXT"]))
                for part in resp.candidates[0].content.parts:
                    if getattr(part, "inline_data", None) and part.inline_data.data:
                        out.write_bytes(part.inline_data.data)
                        return out
            except Exception as e:
                log.info("Gemini image %s failed: %s", m, str(e)[:120])
        _gemini_image_ok = False  # free tier usually has no image quota; don't keep trying this session
    except Exception:
        _gemini_image_ok = False
    return None


def _pollinations(prompt, out, w=1280, h=896):
    url = f"https://image.pollinations.ai/prompt/{quote(prompt[:400])}?width={w}&height={h}&nologo=true&model=flux"
    for _ in range(2):
        try:
            resp = requests.get(url, timeout=90)
            if resp.ok and resp.headers.get("content-type", "").startswith("image"):
                out.write_bytes(resp.content)
                return out
        except Exception:
            pass
        time.sleep(2)
    return None


def generate_image(prompt, out_path):
    out = Path(out_path).with_suffix(".png")
    out.parent.mkdir(parents=True, exist_ok=True)
    styled = f"{prompt}. Clean, professional, high quality illustration, no text, no watermark"
    return _gemini_image(styled, out) or _pollinations(styled, out)


def generate_many(prompts_and_paths, workers=4):
    with ThreadPoolExecutor(workers) as ex:
        return list(ex.map(lambda pp: generate_image(*pp) if pp[0] else None, prompts_and_paths))


@tool("Generate an image from a description and save it (opens it afterwards).", prompt="what to draw")
def make_image(prompt):
    import datetime
    import subprocess
    out = config.OUTPUT_DIR / "images" / f"image_{datetime.datetime.now():%Y%m%d_%H%M%S}.png"
    p = generate_image(prompt, out)
    if not p:
        return "Image generation failed (no free image service reachable)."
    subprocess.run(["open", str(p)])
    return f"Image saved: {p}"
