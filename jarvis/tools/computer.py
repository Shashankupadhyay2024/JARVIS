"""Control of your Mac: shell, apps, AppleScript, screen, Python."""
import datetime
import platform
import subprocess
import sys
import tempfile
from pathlib import Path

from .. import config
from ..llm import router
from ..safety import applescript_is_risky, confirmer, python_is_risky, shell_is_risky
from .registry import tool

IS_MAC = platform.system() == "Darwin"


def _trim(s, n=6000):
    s = s or ""
    return s if len(s) <= n else s[:n] + f"\n...[truncated {len(s) - n} chars]"


@tool("Run a shell (zsh) command on the Mac and return its output. Use for anything the other tools don't cover: "
      "git, file ops, system info, installing, etc. Risky commands ask the user first.",
      command="the command line", timeout="seconds")
def run_shell(command, timeout=120):
    if shell_is_risky(command) and not confirmer.confirm(f"Run this command?\n    {command}"):
        return "User declined; command not run."
    try:
        p = subprocess.run(command, shell=True, capture_output=True, text=True, timeout=timeout,
                           executable="/bin/zsh" if IS_MAC else "/bin/bash", cwd=str(Path.home()))
    except subprocess.TimeoutExpired:
        return f"Command timed out after {timeout}s"
    out = (p.stdout or "") + (("\nSTDERR:\n" + p.stderr) if p.stderr.strip() else "")
    return f"exit={p.returncode}\n{_trim(out)}"


@tool("Run AppleScript to control Mac apps (Music, Mail drafts, Finder, Calendar, Notes, System Events, volume...). "
      "Scripts that send, delete or type keystrokes ask the user first.", script="AppleScript source")
def applescript(script):
    if not IS_MAC:
        return "AppleScript only works on macOS."
    if applescript_is_risky(script) and not confirmer.confirm(f"Run this AppleScript?\n{script}"):
        return "User declined."
    p = subprocess.run(["osascript", "-e", script], capture_output=True, text=True, timeout=60)
    return _trim(p.stdout.strip() or p.stderr.strip() or "done")


@tool("Open (launch or focus) a Mac application by name, e.g. 'Safari', 'Spotify', 'Visual Studio Code'.", name="app name")
def open_app(name):
    p = subprocess.run(["open", "-a", name], capture_output=True, text=True)
    return f"Opened {name}" if p.returncode == 0 else f"Could not open {name}: {p.stderr.strip()}"


@tool("Open a URL in the default browser.", url="full URL")
def open_url(url):
    if not url.startswith(("http://", "https://", "file://")):
        url = "https://" + url
    subprocess.run(["open", url])
    return f"Opened {url}"


@tool("Open a file or folder with its default app (Finder, Preview, Word, Keynote...).", path="file or folder path")
def open_path(path):
    p = Path(path).expanduser()
    if not p.exists():
        return f"Not found: {p}"
    subprocess.run(["open", str(p)])
    return f"Opened {p}"


@tool("Find files on the Mac by name or content using Spotlight. Returns up to 20 paths, newest first.",
      query="words in the file name or content", name_only="True to match file names only")
def find_files(query, name_only=True):
    if IS_MAC:
        cmd = ["mdfind", "-name", query] if name_only else ["mdfind", query]
        p = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
        paths = [x for x in p.stdout.splitlines() if "/Library/" not in x and "/." not in x]
    else:
        p = subprocess.run(["find", str(Path.home()), "-iname", f"*{query}*", "-not", "-path", "*/.*"],
                           capture_output=True, text=True, timeout=30)
        paths = p.stdout.splitlines()
    paths = sorted(paths, key=lambda x: Path(x).stat().st_mtime if Path(x).exists() else 0, reverse=True)[:20]
    return "\n".join(paths) or "No matches."


@tool("Take a screenshot of the screen and answer a question about what's on it.", question="what to look for")
def look_at_screen(question="Describe what is on my screen."):
    shot = Path(tempfile.mkdtemp()) / "screen.png"
    if IS_MAC:
        subprocess.run(["screencapture", "-x", str(shot)], check=True)
    else:
        return "Screen capture only implemented for macOS."
    return router().ask(question, images=[shot], task="vision",
                        system="You are looking at the user's screen. Be specific and concise.")


@tool("Execute Python code for calculations, data analysis, charts (matplotlib) or file conversion. "
      f"Print results. Save any output files under {config.OUTPUT_DIR}.", code="python source")
def run_python(code):
    if python_is_risky(code) and not confirmer.confirm("Run this Python code?\n" + code[:1500]):
        return "User declined."
    f = Path(tempfile.mkdtemp()) / "snippet.py"
    f.write_text("import matplotlib\nmatplotlib.use('Agg')\n" + code if "matplotlib" in code else code)
    p = subprocess.run([sys.executable, str(f)], capture_output=True, text=True, timeout=300, cwd=str(config.OUTPUT_DIR))
    return f"exit={p.returncode}\n{_trim(p.stdout + ('' if not p.stderr else chr(10) + 'STDERR: ' + p.stderr))}"


@tool("Show a macOS notification banner.", title="title", message="body text")
def notify(title, message):
    if IS_MAC:
        esc = lambda s: s.replace('"', '\\"')
        subprocess.run(["osascript", "-e", f'display notification "{esc(message)}" with title "{esc(title)}"'])
    return "Notification shown."


@tool("Get the current date/time and basic system status (battery, disk, Wi-Fi).")
def system_status():
    parts = [datetime.datetime.now().strftime("%A %B %d %Y, %I:%M %p")]
    if IS_MAC:
        for cmd in (["pmset", "-g", "batt"], ["df", "-h", "/"]):
            try:
                parts.append(subprocess.run(cmd, capture_output=True, text=True, timeout=10).stdout.strip())
            except Exception:
                pass
    return "\n".join(parts)
