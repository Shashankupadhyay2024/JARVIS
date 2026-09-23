"""The reasoning loop: think → pick a tool → observe → verify → answer."""
import datetime
import json
import threading

from . import config, learning, memory, ui
from .llm import parse_json, router
from .tools import registry
# importing registers the tools
from .tools import brain, computer, docs, files, images, research, slides, teach, web  # noqa: F401

MAX_STEPS = 12

SYSTEM = """You are JARVIS, {user}'s personal AI assistant and teacher, running on their Mac with full access to it through tools.
Personality: calm, capable, quietly witty (think a loyal British butler who is also a brilliant professor). Warm but never gushing.

HOW YOU THINK (critical thinking & problem solving):
1. Identify the real goal behind the request, not just the literal words.
2. Question assumptions. If the request is ambiguous AND a wrong guess would waste real effort, ask ONE short clarifying question; otherwise make a sensible choice and say which.
3. For facts that change over time (news, prices, versions, people's roles), use web_search instead of memory.
4. Break big jobs into steps and use tools. Prefer the specialised tools (research, make_slides, teach, analyze_file, write_document) over improvising with the shell.
5. After every tool result, check whether it actually achieved the goal. If it failed, diagnose why and try a different approach (at most 2 retries), then be honest about what didn't work.
6. Never claim you did something you didn't. Never invent citations, numbers or file paths.
7. In normal conversation, be a thoughtful companion: engage with ideas, give your honest view with reasons, and push back politely when something seems wrong.

TOOLS:
{tools}

RESPONSE PROTOCOL — reply with exactly ONE JSON object and nothing else:
  To use a tool:   {{"thought": "<short reasoning>", "tool": "<tool name>", "args": {{...}}}}
  To reply:        {{"thought": "<short reasoning>", "say": "<what you say aloud>", "display": "<optional longer text/markdown to show on screen>"}}
"say" is spoken, so keep it natural and brief (1-4 sentences) unless the user asked you to explain something in depth or you're chatting; put long lists, code, or tables in "display".
Files the user wants analysed are usually in {inbox}, Desktop, Downloads or Documents; use find_files if unsure. Save outputs in {outdir}.
Now: {now}.{memory}{lessons}"""


class Agent:
    def __init__(self):
        self.history = []  # only user messages and final replies (tool traces stay within a turn)
        threading.Thread(target=brain.maybe_auto_evolve, daemon=True).start()

    def system_prompt(self, user_msg):
        return SYSTEM.format(
            user=config.USER_NAME, tools=registry.describe(), inbox=config.INBOX_DIR, outdir=config.OUTPUT_DIR,
            now=datetime.datetime.now().strftime("%A %d %B %Y, %I:%M %p"),
            memory=memory.context_block(user_msg), lessons=learning.lessons_block(user_msg))

    def handle(self, user_msg, on_tool=None):
        """Returns dict(say=..., display=...)."""
        llm = router()
        system = self.system_prompt(user_msg)
        msgs = self.history[-14:] + [{"role": "user", "content": user_msg}]
        had_error, used = False, []
        for step in range(MAX_STEPS):
            raw = llm.chat(msgs, system=system, json_mode=True, task="agent", temperature=0.3)
            try:
                act = parse_json(raw)
                if isinstance(act, list):
                    act = act[0]
            except ValueError:
                act = {"say": raw}
            if act.get("tool"):
                name, args = act["tool"], act.get("args") or {}
                used.append(name)
                if on_tool:
                    on_tool(name, args, act.get("thought", ""))
                result = registry.call(name, args)
                if result.startswith("ERROR"):
                    had_error = True
                msgs.append({"role": "assistant", "content": json.dumps(act)})
                msgs.append({"role": "user", "content": f"TOOL RESULT from {name}:\n{result[:7000]}\n\n"
                             "Check: did this achieve the goal? Continue with another tool or reply."})
                continue
            reply = {"say": str(act.get("say") or act.get("response") or "").strip(),
                     "display": str(act.get("display") or "").strip()}
            if not reply["say"] and not reply["display"]:
                reply["say"] = "Done."
            break
        else:
            reply = {"say": "I've taken quite a few steps without finishing, so I'm stopping here to avoid going in circles. "
                            "Here's where things stand.", "display": msgs[-1]["content"][:3000]}
        self.history += [{"role": "user", "content": user_msg},
                         {"role": "assistant", "content": reply["say"] + ("\n" + reply["display"] if reply["display"] else "")}]
        threading.Thread(target=self._after, args=(user_msg, reply, had_error, used), daemon=True).start()
        return reply

    def _after(self, user_msg, reply, had_error, used):
        """Remember the exchange; if something went wrong along the way, reflect and store a lesson."""
        try:
            memory.mem().add(f"User: {user_msg[:300]} | JARVIS: {reply['say'][:300]}", {"kind": "conversation"})
            if had_error:
                d = router().ask_json(
                    f"Task: {user_msg}\nTools used: {used}\nFinal reply: {reply['say']}\n\nA tool failed during this task. "
                    "Write ONE short, general lesson that would help avoid the failure next time, or an empty string if "
                    "there's nothing general to learn. JSON: {\"lesson\": str}", task="reflection", temperature=0.2)
                if d.get("lesson"):
                    learning.add_lesson(d["lesson"], "reflection")
        except Exception:
            pass
