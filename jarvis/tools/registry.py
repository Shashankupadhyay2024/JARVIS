"""Tool registry the agent can call."""
import inspect
import traceback

TOOLS = {}


def tool(description, **arg_docs):
    def deco(fn):
        sig = inspect.signature(fn)
        args = {}
        for name, p in sig.parameters.items():
            doc = arg_docs.get(name, "")
            if p.default is not inspect.Parameter.empty:
                doc += f" (optional, default {p.default!r})"
            args[name] = doc
        TOOLS[fn.__name__] = {"fn": fn, "description": description, "args": args}
        return fn
    return deco


def describe():
    lines = []
    for name, t in TOOLS.items():
        arg_s = ", ".join(f"{a}: {d}" if d else a for a, d in t["args"].items())
        lines.append(f"- {name}({arg_s}) — {t['description']}")
    return "\n".join(lines)


def call(name, args):
    if name not in TOOLS:
        return f"ERROR: unknown tool '{name}'. Available: {', '.join(TOOLS)}"
    fn = TOOLS[name]["fn"]
    valid = set(TOOLS[name]["args"])
    args = {k: v for k, v in (args or {}).items() if k in valid}
    try:
        out = fn(**args)
    except TypeError as e:
        return f"ERROR: bad arguments for {name}: {e}"
    except Exception as e:
        return f"ERROR in {name}: {e}\n{traceback.format_exc(limit=2)}"
    return out if isinstance(out, str) else str(out)
