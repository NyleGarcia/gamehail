#!/usr/bin/env bash
# System packages gamepilot needs beyond the Python venv (Arch / CachyOS).
set -euo pipefail

sudo pacman -S --needed --noconfirm \
  python spectacle ffmpeg alsa-utils libpulse pipewire-audio portaudio

# Keyboard/HOTAS hotkeys are off by default - the deck plugin and `gamepilot ctl` need
# none of this. Only if you turn [hotkeys] enabled on does reading /dev/input matter,
# and that needs the `input` group:
#
#   sudo usermod -aG input "$USER"   # then log out and back in
if ! id -nG "$USER" | grep -qw input; then
  echo "note: not in the 'input' group - fine unless you enable keyboard hotkeys"
fi

echo "now run: uv sync && ./scripts/fetch-voice.sh"
