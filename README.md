# JARVIS — personal assistant & teacher

Local-first: **Gemini (free tier)** does the heavy thinking; when its quota runs out, **Ollama (Llama 3.1 8B)** takes over automatically, and it learns from Gemini's past answers.

## Install (macOS)

```bash
cd ~/Downloads && unzip -o JARVIS.zip && cd JARVIS && bash install.sh
```

Then open **JARVIS** from Launchpad, Spotlight (⌘Space → "JARVIS") or Applications. Drag it to your Dock to keep it one click away. You can still run `jarvis` in Terminal.

### The app
- Clicking the icon starts JARVIS in the background and opens the HUD window. No Terminal needed.
- Clicking it while JARVIS is already running just reopens the HUD.
- **⏻** in the top-right shuts JARVIS down. Closing the HUD window also shuts it down after about 30 seconds.
- Logs: `~/.jarvis/app.log`. On first launch, allow the **microphone** for JARVIS (System Settings → Privacy & Security → Microphone).

Get a free Gemini key at <https://aistudio.google.com/apikey>. The installer asks for it (you can leave it blank and run fully local).

## The HUD

When you start `jarvis`, a holographic control window opens alongside the terminal:
- **Core:** the centre rings react live, pulsing with your voice when listening, turning amber when processing and animating when speaking.
- **Dialogue:** a live transcript. Type in the command bar, or press 🎙 (or **⌘K**) to talk. You don't need the wake word there.
- **Drop any file** on the window to copy it to your inbox and have it analysed.
- **Protocols:** one-click Research, Teach, Slides, Document, Scan screen, Status and Learning.
- **Activity:** every tool JARVIS runs, as it happens. **Recent output:** click to open reports, decks and videos.
- **Systems / Neural core:** CPU, memory, storage, battery, which model is answering, and learning progress.
- **Authorization:** risky actions pop up a prompt with **Authorize / Deny** buttons.
- **Header:** click the EARS pill to toggle wake-word listening; 🔇 mutes spoken replies.

Run `jarvis --no-ui` to skip the window. The HUD only listens on your Mac (127.0.0.1) and needs a per-session key, so other websites can't send it commands.

## Using it

| Command | What it does |
|---|---|
| `jarvis` | **Talk or type — both work at once.** Say **"Jarvis, …"**, or just type in the Terminal window and press Enter. For ~8 s after a reply you can follow up by voice without the wake word. |
| `jarvis --ptt` | Press Enter, then speak (good in noisy rooms). Typing still works. |
| `jarvis --text` | Typing only, microphone off. Drag a file into Terminal to paste its path. `--mute` silences replies. |
| `jarvis research "calorie counting apps" -n 8 --min 80 --slides` | Cited Word report (+ optional deck). |
| `jarvis teach "photosynthesis" --slides 8 --video` | Interactive narrated lesson in your browser (+ MP4). |
| `jarvis teach "exam review" --file ~/Downloads/lecture.pdf` | Teaches from your own notes. |
| `jarvis analyze ~/Downloads/data.xlsx "what trends stand out?"` | Analyses any file. |
| `jarvis slides "intro to RAG" --slides 12` | PowerPoint with images and speaker notes. |
| `jarvis doctor` | Checks everything is installed. |
| `jarvis evolve` | Bakes what it has learned into a custom local model. |

Things to say: *"Jarvis, research calorie counting apps and make slides from it"*, *"teach me linear regression like a professor"*, *"what's on my screen?"*, *"summarise the newest PDF in my Downloads"*, *"open Spotify and play something calm"*, *"remember that my exams are in December"*, *"learn that I prefer MLA citations"*.

### Typing commands (any mode)

| Type | Effect |
|---|---|
| `/mute` / `/unmute` | Turn spoken replies off/on (for class or the library). |
| `/mic off` / `/mic on` | Stop/start listening, e.g. when you can't talk. |
| Enter (while it's talking) | Stops it talking. |
| `y` | Approves a risky action (instead of saying "confirm"). |
| `/help` | Lists these commands. |

## The classroom

`teach` opens a local page where JARVIS presents slide by slide in a narrated voice, revealing bullets as it talks.
- **Raise your hand** with **Q** or the ✋ button and ask out loud. It stops when you stop talking. You can also type.
- It answers in the context of the current slide, writes key points on a "whiteboard", then resumes the lecture.
- **Space** play/pause · **←/→** slides · **C** captions · **🎬 Video** exports an MP4.
- Lessons are saved in `~/Documents/JARVIS/lectures/` (with a `.pptx` and a log of your questions).

## Research and credibility

Research uses Google Scholar through a visible Chrome window. If Google shows a CAPTCHA, solve it and research continues on its own. Your Chrome profile is kept, so CAPTCHAs become rare. Every result is enriched with OpenAlex data (DOI, full abstract, citation count, retraction status) and scored from 0 to 100:

| Signal | Points |
|---|---|
| Citation impact (adjusted for age) | 35 |
| Venue quality (journal/conference, reputable publisher) | 25 |
| Verification (DOI, multiple authors, full text) | 15 |
| Recency | 15 |
| Depth of content | 10 |

Retracted papers score 0. Only papers at or above your threshold (default 80) that also pass an AI relevance check are kept. It keeps paging until it has exactly the number you asked for. If there aren't enough, it tells you instead of padding the list. If Scholar is blocked it falls back to OpenAlex.

## How it gets smarter

1. **Imitation.** Every Gemini answer is saved. When running locally, the most similar saved answers are shown to Llama as worked examples.
2. **Lessons.** Your corrections ("Jarvis, learn that…") and its own reflections after a failed step become rules injected into future prompts.
3. **Evolve.** `jarvis evolve` (also automatic after every 60 new examples) builds a custom `jarvis-brain` Ollama model with those lessons and examples built in.
4. `~/.jarvis/data/finetune.jsonl` accumulates chat-format training data if you ever want to properly fine-tune (e.g. with `mlx-lm` LoRA on your Mac).

## Safety

JARVIS has full control of your Mac, but it asks first (spoken **"confirm"** or typed **y**) before deleting, moving, running `sudo`, installing packages, sending anything, sending keystrokes, or overwriting files.

## Settings — `~/.jarvis/.env`

```
GEMINI_API_KEY=...
JARVIS_USER_NAME=Shashank
OLLAMA_MODEL=llama3.1:8b
JARVIS_TTS_VOICE=en-GB-RyanNeural     # any edge-tts voice
JARVIS_SAY_VOICE=Daniel               # offline fallback voice
JARVIS_WHISPER_MODEL=base.en          # small.en = more accurate, slower
JARVIS_MIN_CREDIBILITY=80
JARVIS_CONTACT_EMAIL=you@example.com  # optional, faster OpenAlex
```

## Permissions macOS will ask for

Microphone (voice), Accessibility (AppleScript keystrokes), Screen Recording (for "what's on my screen"). Grant these to **Terminal** in System Settings → Privacy & Security.

## Known limits

- Gemini's free tier has no image generation, so slide images come from Pollinations.ai (free). If it's down, slides are built without images.
- Voice uses free edge-tts neural voices, which need internet. Offline, it falls back to the macOS `Daniel` voice.
- Llama 8B is noticeably weaker than Gemini at long multi-step tasks. For big jobs, it's best when Gemini quota is available.
