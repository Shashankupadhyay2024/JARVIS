"""Shared output helpers so long-running tools can report progress in the terminal and by voice."""
import logging

from . import config

logging.basicConfig(filename=str(config.LOG_FILE), level=logging.INFO,
                    format="%(asctime)s %(name)s %(levelname)s %(message)s")

try:
    from rich.console import Console
    console = Console()
except Exception:  # pragma: no cover
    console = None

speaker = None  # set by main when voice is on


def status(msg):
    if console:
        console.print(f"[dim cyan]  ⋯ {msg}[/]")
    else:
        print(f"  ... {msg}")
    logging.getLogger("jarvis").info(msg)
    try:
        from . import bus
        bus.publish("activity", text=str(msg))
    except Exception:
        pass


def say(msg, block=False):
    """Short spoken update (non-blocking by default)."""
    status(msg)
    if speaker:
        speaker.say(msg, block=block)
