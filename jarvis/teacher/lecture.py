"""Builds a narrated lecture: slide spec + images + one audio file per slide."""
import datetime
import json
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from .. import config, ui
from ..tools.docs import slug
from ..tools.slides import build_pptx, deck_spec, render_images
from ..voice import synth_to_file


def build_lecture(topic, n_slides=8, source_text="", audience="a curious college student", images=True):
    ui.say(f"Preparing your lesson on {topic}. This takes a minute or two.")
    spec = deck_spec(topic, n_slides, source_text, audience, teaching=True)
    folder = config.LECTURE_DIR / f"{slug(topic)}_{datetime.datetime.now():%Y%m%d_%H%M}"
    (folder / "audio").mkdir(parents=True, exist_ok=True)

    first = spec["slides"][0]["title"] if spec["slides"] else topic
    spec["intro_notes"] = (f"Hello {config.USER_NAME}, and welcome. Today's lesson is {spec['title']}. "
                           f"{spec.get('subtitle', '')}. We'll move through {len(spec['slides'])} short sections, starting with "
                           f"{first}. If anything is unclear, raise your hand at any time: press the hand button or the Q key, "
                           f"and just ask. Let's begin.")
    recap = "; ".join(s["title"] for s in spec["slides"])
    spec["outro_notes"] = (f"That brings us to the end. Quick recap: we covered {recap}. "
                           f"Try the short quiz on screen to check your understanding, and feel free to ask me anything else.")

    imgs = render_images(spec, folder / "images") if images else {}
    for i, s in enumerate(spec["slides"]):
        if i in imgs:
            s["image"] = str(Path(imgs[i]).relative_to(folder))

    scripts = [("00_intro", spec["intro_notes"])] + \
              [(f"{i + 1:02d}", s.get("notes") or s["title"]) for i, s in enumerate(spec["slides"])] + \
              [(f"{len(spec['slides']) + 1:02d}_outro", spec["outro_notes"])]
    ui.status(f"Recording narration for {len(scripts)} slides")

    def rec(item):
        name, text = item
        return str(synth_to_file(text, folder / "audio" / name).relative_to(folder))
    with ThreadPoolExecutor(4) as ex:
        audio = list(ex.map(rec, scripts))
    spec["intro_audio"], spec["outro_audio"] = audio[0], audio[-1]
    for s, a in zip(spec["slides"], audio[1:-1]):
        s["audio"] = a

    (folder / "lecture.json").write_text(json.dumps(spec, indent=2))
    try:
        build_pptx(spec, folder / "slides.pptx", {i: folder / s["image"] for i, s in enumerate(spec["slides"]) if s.get("image")})
    except Exception as e:
        ui.status(f"(pptx export skipped: {e})")
    return folder
