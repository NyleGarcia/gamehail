#!/usr/bin/env bash
# Ask KWin to keep the gamepilot overlay above other windows.
# Wayland ignores client-side stay-on-top, so the rule has to live in KWin.
set -euo pipefail

RULES="${XDG_CONFIG_HOME:-$HOME/.config}/kwinrulesrc"
if grep -q "gamepilot-overlay" "$RULES" 2>/dev/null; then
  echo "rule already present in $RULES"; exit 0
fi

n=$(kreadconfig6 --file kwinrulesrc --group General --key count 2>/dev/null || echo 0)
idx=$((n + 1))
w() { kwriteconfig6 --file kwinrulesrc --group "$idx" --key "$1" "$2"; }
w Description "gamepilot overlay"
w title "gamepilot-overlay"
w titlematch 1
w above true
w aboverule 3
w skiptaskbar true
w skiptaskbarrule 3
w skipswitcher true
w skipswitcherrule 3
w skippager true
w skippagerrule 3
w focus 0
w focusrule 3
kwriteconfig6 --file kwinrulesrc --group General --key count "$idx"
qdbus6 org.kde.KWin /KWin reconfigure 2>/dev/null || true
echo "installed KWin rule #$idx - run the game in borderless-window mode for the overlay to show"
