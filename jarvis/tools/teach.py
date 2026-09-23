"""Teacher mode tools."""
from .. import ui
from .registry import tool


@tool("Teach the user a topic: builds a narrated, illustrated lesson and opens an interactive classroom in the browser "
      "where JARVIS presents like a professor and the user can raise their hand and ask questions at any point. "
      "Can be based on a file (lecture notes, PDF, research report). Optionally also exports an MP4 video.",
      topic="what to teach", slides="number of lesson slides", source_file="optional file to teach from",
      audience="level of the learner", video="also export an MP4 video lecture")
def teach(topic, slides=8, source_file="", audience="a curious college student", video=False):
    from ..teacher.lecture import build_lecture
    from ..teacher.server import serve
    from .files import extract_text
    src = extract_text(source_file) if source_file else ""
    folder = build_lecture(topic, int(slides), src, audience)
    url = serve(folder)
    msg = f"Lesson ready and open in your browser: {url}\nLesson files: {folder}"
    if str(video).lower() in ("true", "1", "yes"):
        from ..teacher.video import export_video
        ui.status("Rendering video")
        msg += f"\nVideo: {export_video(folder)}"
    return msg


@tool("Create an MP4 video lecture (narrated slides) on a topic without opening the classroom.",
      topic="what the video teaches", slides="number of slides", source_file="optional file to base it on")
def make_video_lesson(topic, slides=8, source_file=""):
    import subprocess
    from ..teacher.lecture import build_lecture
    from ..teacher.video import export_video
    from .files import extract_text
    src = extract_text(source_file) if source_file else ""
    folder = build_lecture(topic, int(slides), src)
    ui.status("Rendering video")
    out = export_video(folder)
    subprocess.run(["open", "-R", str(out)])
    return f"Video saved: {out}"


@tool("Re-open a previous lesson in the classroom. Lists lessons if name is empty.", name="part of the lesson folder name")
def open_lesson(name=""):
    from .. import config
    from ..teacher.server import serve
    lessons = sorted(config.LECTURE_DIR.glob("*/lecture.json"), key=lambda p: p.stat().st_mtime, reverse=True)
    if not name:
        return "\n".join(p.parent.name for p in lessons[:20]) or "No lessons yet."
    for p in lessons:
        if name.lower().replace(" ", "_") in p.parent.name.lower():
            return f"Opened: {serve(p.parent)}"
    return f"No lesson matching '{name}'."
