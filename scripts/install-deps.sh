#!/usr/bin/env bash
# System packages gamepilot needs beyond the Python venv (Arch / CachyOS).
set -euo pipefail

sudo pacman -S --needed --noconfirm \
  python spectacle ffmpeg alsa-utils libpulse pipewire-audio portaudio

# evdev hotkeys need read access to /dev/input/event*
if ! id -nG "$USER" | grep -qw input; then
  echo "adding $USER to the 'input' group (log out and back in afterwards)"
  sudo usermod -aG input "$USER"
fi

echo "now run: uv sync && ./scripts/fetch-voice.sh"
