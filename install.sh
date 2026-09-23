#!/bin/bash
# JARVIS installer for macOS (Apple Silicon or Intel). Safe to re-run.
set -e
cd "$(dirname "$0")"
JDIR="$(pwd)"
CYAN='\033[0;36m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'; NC='\033[0m'
step() { echo -e "\n${CYAN}▶ $1${NC}"; }

step "Checking Homebrew"
if ! command -v brew >/dev/null 2>&1; then
  /bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)"
  eval "$(/opt/homebrew/bin/brew shellenv 2>/dev/null || /usr/local/bin/brew shellenv)"
fi

step "Installing system tools (Python 3.12, ffmpeg, PortAudio, Ollama)"
brew install python@3.12 ffmpeg portaudio ollama >/dev/null || true
if [ ! -d "/Applications/Google Chrome.app" ]; then
  step "Installing Google Chrome (used for Google Scholar)"
  brew install --cask google-chrome || true
fi

step "Starting Ollama"
brew services start ollama >/dev/null 2>&1 || (nohup ollama serve >/dev/null 2>&1 &)
for i in {1..20}; do curl -s http://127.0.0.1:11434/api/tags >/dev/null && break; sleep 1; done

step "Downloading local models (first time ≈ 6 GB, grab a coffee)"
ollama pull llama3.1:8b
ollama pull nomic-embed-text
ollama pull moondream

step "Creating Python environment"
PY="$(brew --prefix)/bin/python3.12"
"$PY" -m venv .venv
.venv/bin/python -m pip install --upgrade pip -q
.venv/bin/python -m pip install -r requirements.txt -q

step "Pre-loading speech recognition model"
.venv/bin/python -c "from faster_whisper import WhisperModel; WhisperModel('base.en', device='cpu', compute_type='int8')" >/dev/null

step "Configuring"
mkdir -p "$HOME/.jarvis"
ENV="$HOME/.jarvis/.env"
touch "$ENV"; chmod 600 "$ENV"
if ! grep -q "^JARVIS_USER_NAME=" "$ENV"; then
  read -r -p "What should JARVIS call you? [Shashank] " NAME
  echo "JARVIS_USER_NAME=${NAME:-Shashank}" >> "$ENV"
fi
if ! grep -q "^GEMINI_API_KEY=" "$ENV"; then
  echo -e "${YELLOW}Get a free Gemini key at https://aistudio.google.com/apikey (leave blank to run fully local).${NC}"
  read -r -s -p "Paste your Gemini API key: " KEY; echo
  [ -n "$KEY" ] && echo "GEMINI_API_KEY=$KEY" >> "$ENV"
fi

step "Adding the 'jarvis' command"
mkdir -p "$HOME/.local/bin"
cat > "$HOME/.local/bin/jarvis" <<EOF
#!/bin/bash
cd "$JDIR" && exec "$JDIR/.venv/bin/python" -m jarvis.main "\$@"
EOF
chmod +x "$HOME/.local/bin/jarvis"
grep -q '.local/bin' "$HOME/.zshrc" 2>/dev/null || echo 'export PATH="$HOME/.local/bin:$PATH"' >> "$HOME/.zshrc"
export PATH="$HOME/.local/bin:$PATH"

step "Building JARVIS.app (Applications, Launchpad, Spotlight)"
APPDIR="/Applications"; [ -w "$APPDIR" ] || APPDIR="$HOME/Applications"; mkdir -p "$APPDIR"
APP="$APPDIR/JARVIS.app"
rm -rf "$APP"
mkdir -p "$APP/Contents/MacOS" "$APP/Contents/Resources"
ICONSET="$(mktemp -d)/JARVIS.iconset"; mkdir -p "$ICONSET"
for s in 16 32 128 256 512; do
  sips -z $s $s assets/icon.png --out "$ICONSET/icon_${s}x${s}.png" >/dev/null
  sips -z $((s*2)) $((s*2)) assets/icon.png --out "$ICONSET/icon_${s}x${s}@2x.png" >/dev/null
done
iconutil -c icns "$ICONSET" -o "$APP/Contents/Resources/JARVIS.icns"
cat > "$APP/Contents/Info.plist" <<PLIST
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
  <key>CFBundleName</key><string>JARVIS</string>
  <key>CFBundleDisplayName</key><string>JARVIS</string>
  <key>CFBundleIdentifier</key><string>com.shashank.jarvis</string>
  <key>CFBundleExecutable</key><string>JARVIS</string>
  <key>CFBundleIconFile</key><string>JARVIS</string>
  <key>CFBundlePackageType</key><string>APPL</string>
  <key>CFBundleShortVersionString</key><string>2.0</string>
  <key>CFBundleVersion</key><string>2</string>
  <key>LSMinimumSystemVersion</key><string>11.0</string>
  <key>NSMicrophoneUsageDescription</key><string>JARVIS listens for your voice commands.</string>
  <key>NSAppleEventsUsageDescription</key><string>JARVIS controls apps when you ask it to.</string>
</dict></plist>
PLIST
cat > "$APP/Contents/MacOS/JARVIS" <<LAUNCH
#!/bin/bash
# JARVIS.app launcher: starts JARVIS in the background and opens the HUD. If it's already running, just reopens the HUD.
JDIR="$JDIR"
export PATH="\$HOME/.local/bin:/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin"
LOG="\$HOME/.jarvis/app.log"; mkdir -p "\$HOME/.jarvis"
[ -f "\$LOG" ] && [ "\$(stat -f%z "\$LOG")" -gt 5000000 ] && mv "\$LOG" "\$LOG.old"
if curl -s -m 1 http://127.0.0.1:5056/api/status >/dev/null 2>&1; then
  KEY="\$(cat "\$HOME/.jarvis/hud.key" 2>/dev/null)"
  open -na "Google Chrome" --args --app="http://127.0.0.1:5056/#k=\$KEY" --window-size=1440,900 --user-data-dir="\$HOME/.jarvis/hud-profile"
  exit 0
fi
pgrep -xq ollama || brew services start ollama >/dev/null 2>&1 || (nohup ollama serve >/dev/null 2>&1 &)
cd "\$JDIR" && nohup "\$JDIR/.venv/bin/python" -m jarvis.main --app >> "\$LOG" 2>&1 &
disown
exit 0
LAUNCH
chmod +x "$APP/Contents/MacOS/JARVIS"
codesign --force --deep -s - "$APP" >/dev/null 2>&1 || true
touch "$APP"; /System/Library/Frameworks/CoreServices.framework/Frameworks/LaunchServices.framework/Support/lsregister -f "$APP" >/dev/null 2>&1 || true
echo "   Installed: $APP"

step "Health check"
"$HOME/.local/bin/jarvis" doctor || true

echo -e "\n${GREEN}✔ JARVIS is installed.${NC}"
echo "Launch it from Launchpad or Spotlight (⌘ Space → JARVIS), or drag JARVIS from Applications to your Dock."
echo "Terminal still works too: open a NEW Terminal window and run  jarvis"
echo "(The first time, macOS will ask to let Terminal use the microphone — click Allow.)"
