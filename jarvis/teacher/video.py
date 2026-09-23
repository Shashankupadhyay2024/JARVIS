"""Turns a lecture folder into an MP4 (slides rendered with Pillow + narration audio, joined with ffmpeg)."""
import json
import shutil
import subprocess
import textwrap
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

W, H = 1920, 1080
BG, ACCENT, TEXT, MUTED = (11, 18, 32), (53, 200, 255), (242, 245, 250), (154, 168, 191)
FONT_CANDIDATES = ["/System/Library/Fonts/Supplemental/Arial.ttf", "/System/Library/Fonts/Helvetica.ttc",
                   "/Library/Fonts/Arial.ttf", "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"]
BOLD_CANDIDATES = ["/System/Library/Fonts/Supplemental/Arial Bold.ttf", "/System/Library/Fonts/Helvetica.ttc",
                   "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"]


def _font(size, bold=False):
    for f in (BOLD_CANDIDATES if bold else FONT_CANDIDATES):
        if Path(f).exists():
            try:
                return ImageFont.truetype(f, size)
            except Exception:
                continue
    return ImageFont.load_default()


def render_slide(title, bullets, image, out, subtitle="", number=None):
    im = Image.new("RGB", (W, H), BG)
    d = ImageDraw.Draw(im)
    text_w = 1100 if image else 1700
    if bullets is None:  # title / end card
        y = 380
        for line in textwrap.wrap(title, 36):
            d.text((120, y), line, font=_font(84, True), fill=TEXT)
            y += 100
        d.rectangle([120, y + 20, 320, y + 28], fill=ACCENT)
        for line in textwrap.wrap(subtitle, 60):
            d.text((120, y + 60), line, font=_font(40), fill=MUTED)
            y += 52
    else:
        y = 80
        for line in textwrap.wrap(title, 40 if image else 55):
            d.text((120, y), line, font=_font(64, True), fill=TEXT)
            y += 76
        d.rectangle([120, y + 14, 250, y + 20], fill=ACCENT)
        y += 70
        chars = 42 if image else 70
        for b in bullets:
            lines = textwrap.wrap(b, chars)
            d.text((125, y), "•", font=_font(40, True), fill=ACCENT)
            for ln in lines:
                d.text((175, y), ln, font=_font(40), fill=TEXT)
                y += 52
            y += 26
        if image and Path(image).exists():
            pic = Image.open(image).convert("RGB")
            pic.thumbnail((640, 800))
            im.paste(pic, (1200, 260))
        if number:
            d.text((W - 120, H - 70), str(number), font=_font(28), fill=MUTED)
    im.save(out)
    return out


def export_video(folder):
    folder = Path(folder)
    if not shutil.which("ffmpeg"):
        raise RuntimeError("ffmpeg not installed. Run:  brew install ffmpeg")
    spec = json.loads((folder / "lecture.json").read_text())
    work = folder / "video_parts"
    work.mkdir(exist_ok=True)
    frames = [(render_slide(spec["title"], None, None, work / "f00.png", spec.get("subtitle", "")), folder / spec["intro_audio"])]
    for i, s in enumerate(spec["slides"], 1):
        img = folder / s["image"] if s.get("image") else None
        frames.append((render_slide(s["title"], s["bullets"], img, work / f"f{i:02d}.png", number=i + 1), folder / s["audio"]))
    frames.append((render_slide("Recap", [x["title"] for x in spec["slides"]][:8], None, work / "f99.png"), folder / spec["outro_audio"]))
    segs = []
    for n, (img, audio) in enumerate(frames):
        seg = work / f"seg{n:02d}.mp4"
        subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-loop", "1", "-i", str(img), "-i", str(audio),
                        "-c:v", "libx264", "-tune", "stillimage", "-r", "24", "-c:a", "aac", "-b:a", "160k", "-ar", "44100",
                        "-pix_fmt", "yuv420p", "-af", "apad=pad_dur=0.8", "-shortest", str(seg)], check=True)
        segs.append(seg)
    lst = work / "list.txt"
    lst.write_text("".join(f"file '{s.name}'\n" for s in segs))
    out = folder / f"{folder.name}.mp4"
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-f", "concat", "-safe", "0", "-i", str(lst), "-c", "copy", str(out)],
                   check=True, cwd=str(work))
    return out
