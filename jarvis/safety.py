"""Decides which actions need your spoken/typed confirmation before running."""
import re

RISKY_SHELL = [
    r"\brm\b", r"\brmdir\b", r"\bunlink\b", r"\bsudo\b", r"\bmv\b", r"\bdd\b", r"\bmkfs",
    r"\bdiskutil\b", r"\bkill(all)?\b", r"\bpkill\b", r"\bshutdown\b", r"\breboot\b", r"\bhalt\b",
    r"\bchmod\b", r"\bchown\b", r"\bbrew\s+(install|uninstall|remove|upgrade)", r"\bpip3?\s+(install|uninstall)",
    r"\bnpm\s+(i|install|uninstall)\b", r"curl[^|]*\|\s*(ba|z)?sh", r"wget[^|]*\|\s*(ba|z)?sh",
    r"\bgit\s+(push|reset|clean|checkout\s+--|rebase)", r"\blaunchctl\b", r"\bdefaults\s+write\b",
    r"\bcrontab\b", r"(^|[^>])>\s*[~/\w]", r"\btruncate\b", r"\bsrm\b", r"\btrash\b", r"\bosascript\b",
    r"\bmail\b", r"\bsendmail\b", r"\bscp\b", r"\bssh\b", r"\bnetworksetup\b", r"\bcsrutil\b",
]
RISKY_APPLESCRIPT = [r"\bdelete\b", r"\bsend\b", r"\bempty trash\b", r"\bshut down\b", r"\brestart\b",
                     r"\bmove\b.*\bto trash\b", r"\bdo shell script\b", r"\bpurchase\b", r"\bbuy\b",
                     r"\bkeystroke\b", r"\bkey code\b"]
RISKY_PYTHON = [r"\bos\.(remove|unlink|rmdir|system|kill)", r"\bshutil\.(rmtree|move)", r"\bsubprocess\b",
                r"open\([^)]*['\"][wa]b?['\"]", r"\.unlink\(", r"\brequests\.(post|put|delete)", r"\bsmtplib\b",
                r"\.to_(csv|excel|json)\(", r"\.write_(text|bytes)\(", r"\bsavefig\("]


def _match(patterns, text):
    return [p for p in patterns if re.search(p, text, re.IGNORECASE)]


def shell_is_risky(cmd):
    return bool(_match(RISKY_SHELL, cmd))


def applescript_is_risky(script):
    return bool(_match(RISKY_APPLESCRIPT, script))


def python_is_risky(code):
    # Writing into JARVIS's own output folder is fine; anything else destructive needs a yes.
    hits = _match(RISKY_PYTHON, code)
    if hits and all(p in (r"\bsavefig\(", r"\.to_(csv|excel|json)\(", r"\.write_(text|bytes)\(",
                           r"open\([^)]*['\"][wa]b?['\"]") for p in hits) and "Documents/JARVIS" in code:
        return False
    return bool(hits)


class Confirmer:
    """Asks the user before risky actions. The interface (voice/text) plugs in `ask`."""

    def __init__(self):
        self.ask = self._ask_text
        self.auto_yes = False

    @staticmethod
    def _ask_text(question):
        try:
            ans = input(f"\n⚠️  {question} [y/N] ").strip().lower()
        except EOFError:
            return False
        return ans in ("y", "yes")

    def confirm(self, question):
        if self.auto_yes:
            return True
        return self.ask(question)


confirmer = Confirmer()
