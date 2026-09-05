#!/usr/bin/env bash
# Download a piper TTS voice. Usage: ./scripts/fetch-voice.sh [voice]
set -euo pipefail

VOICE="${1:-en_US-amy-medium}"
DEST="${XDG_DATA_HOME:-$HOME/.local/share}/gamepilot/voices"
BASE="https://huggingface.co/rhasspy/piper-voices/resolve/main"

# en_US-amy-medium -> en/en_US/amy/medium
lang_full="${VOICE%%-*}"          # en_US
lang="${lang_full%%_*}"           # en
rest="${VOICE#*-}"                # amy-medium
name="${rest%%-*}"                # amy
quality="${rest##*-}"             # medium
path="$lang/$lang_full/$name/$quality/$VOICE"

mkdir -p "$DEST"
for ext in onnx onnx.json; do
  echo "fetching $VOICE.$ext"
  curl -fL --progress-bar -o "$DEST/$VOICE.$ext" "$BASE/$path.$ext?download=true"
done
echo "voice_model = \"$DEST/$VOICE.onnx\""
