#!/usr/bin/env python3
"""Regenerate star-citizen.toml's [game] vocabulary_generated from live game data.

Source order - local/gamedata first, the live API only as a fallback - is handled by
SCMCP itself, not duplicated here: `sc_get_vocabulary` (SCMCP >= 1.2.0) merges locally
extracted names (SCMCP_GAME_DATA_DIR - ore signatures, blueprints, Wikelo trades; the
words no public API exposes) with tradeable commodity and manufacturer names from the
UEX/SCW APIs, and works with either present. Calling the tool here rather than hitting
UEX/SCW directly means gamehail always sees exactly what the assistant itself can query
mid-game, including whatever local gamedata is configured on this machine.

A checked-in snapshot at src/gamehail/games/data/star-citizen.vocab.json is gamedata in
gamehail's own sense: ships in the repo, needs no network, no token, and no SCMCP
checkout, and is what a fresh clone builds from by default. If SCMCP cannot be reached
(not installed, no UEXTOKEN, no network), the existing snapshot is left alone rather
than the module losing its vocabulary - offline is always a safe fallback.

Usage:
    uv run python scripts/vocab/star-citizen.py            # local snapshot if present
    uv run python scripts/vocab/star-citizen.py --refresh  # force a live re-fetch
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
MODULE_PATH = ROOT / "src" / "gamehail" / "games" / "star-citizen.toml"
SNAPSHOT_PATH = ROOT / "src" / "gamehail" / "games" / "data" / "star-citizen.vocab.json"
SCMCP_ENTRY = Path(os.environ.get("SCMCP_ENTRY", "")) or (Path.home() / "git" / "SCMCP" / "dist" / "index.js")


def _env_with_token() -> dict[str, str]:
    env = dict(os.environ)
    if "UEXTOKEN" not in env:
        secrets = Path.home() / ".secrets" / "scmcp.env"
        if secrets.is_file():
            for line in secrets.read_text().splitlines():
                if line.startswith("UEXTOKEN="):
                    env["UEXTOKEN"] = line.split("=", 1)[1].strip()
    return env


def fetch_from_mcp(timeout: float = 30.0) -> list[str]:
    """One-shot MCP call: initialize, call sc_get_vocabulary, take the result, exit."""
    node = shutil.which("node")
    if not node:
        raise RuntimeError("node not found on PATH")
    if not SCMCP_ENTRY.is_file():
        raise RuntimeError(f"SCMCP not found at {SCMCP_ENTRY} (set SCMCP_ENTRY to override)")

    requests = [
        {"jsonrpc": "2.0", "id": 1, "method": "initialize",
         "params": {"protocolVersion": "2024-11-05", "capabilities": {},
                   "clientInfo": {"name": "gamehail-vocab-builder", "version": "1"}}},
        {"jsonrpc": "2.0", "method": "notifications/initialized"},
        {"jsonrpc": "2.0", "id": 2, "method": "tools/call",
         "params": {"name": "sc_get_vocabulary", "arguments": {
             # Armor and clothes are left out: ~4,100 terms, almost all colour-variant
             # SKUs ("Rating Undersuit - Sand/Black/Red/...") nobody asks about by voice.
             # Weapons, attachments and ship components are things players actually name
             # mid-game ("what shield should I put on my Connie") and stay a sane size.
             "include_armor": False,
             "include_clothes": False,
             "include_components": True,
         }}},
    ]
    stdin = "\n".join(json.dumps(r) for r in requests) + "\n"

    proc = subprocess.run(
        [node, str(SCMCP_ENTRY)], input=stdin, capture_output=True, text=True,
        timeout=timeout, env=_env_with_token(),
    )
    for line in proc.stdout.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            msg = json.loads(line)
        except json.JSONDecodeError:
            continue
        if msg.get("id") == 2:
            if "error" in msg:
                raise RuntimeError(f"sc_get_vocabulary failed: {msg['error']}")
            body = msg["result"]["content"][0]["text"]
            payload = json.loads(body)
            return list(payload["terms"])
    raise RuntimeError(
        f"no response to sc_get_vocabulary (exit {proc.returncode}): {proc.stderr[-500:]}"
    )


def load_snapshot() -> list[str] | None:
    if not SNAPSHOT_PATH.is_file():
        return None
    try:
        return json.loads(SNAPSHOT_PATH.read_text())
    except json.JSONDecodeError:
        return None


def save_snapshot(terms: list[str]) -> None:
    SNAPSHOT_PATH.parent.mkdir(parents=True, exist_ok=True)
    SNAPSHOT_PATH.write_text(json.dumps(sorted(terms), indent=2) + "\n")


def resolve(refresh: bool) -> tuple[list[str], str]:
    if not refresh and (snapshot := load_snapshot()):
        return snapshot, "local snapshot"
    try:
        terms = fetch_from_mcp()
    except (RuntimeError, OSError, subprocess.TimeoutExpired) as exc:
        if snapshot := load_snapshot():
            print(f"SCMCP unavailable ({exc}); using the existing local snapshot instead",
                  file=sys.stderr)
            return snapshot, "local snapshot (MCP fallback failed)"
        raise RuntimeError(f"no local snapshot and SCMCP is unavailable: {exc}") from exc
    save_snapshot(terms)
    return terms, "sc_get_vocabulary (snapshot refreshed)"


def render_block(terms: list[str]) -> str:
    lines = ["vocabulary_generated = ["]
    for term in sorted(set(terms)):
        escaped = term.replace("\\", "\\\\").replace('"', '\\"')
        lines.append(f'  "{escaped}",')
    lines.append("]")
    return "\n".join(lines)


def write_module(block: str) -> None:
    text = MODULE_PATH.read_text()
    pattern = re.compile(r"vocabulary_generated = \[.*?\n\]", re.DOTALL)
    if pattern.search(text):
        text = pattern.sub(block, text, count=1)
    else:
        marker = re.compile(r"(vocabulary_static = \[.*?\n\])", re.DOTALL)
        if marker.search(text):
            text = marker.sub(lambda m: f"{m.group(1)}\n\n{block}", text, count=1)
        else:
            text = text.rstrip() + f"\n\n{block}\n"
    MODULE_PATH.write_text(text)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--refresh", action="store_true",
                        help="skip the local snapshot and re-fetch via SCMCP")
    args = parser.parse_args()

    try:
        terms, source = resolve(args.refresh)
    except RuntimeError as exc:
        print(f"could not build vocabulary: {exc}", file=sys.stderr)
        return 1

    write_module(render_block(terms))
    print(f"wrote {len(set(terms))} generated terms to "
          f"{MODULE_PATH.relative_to(ROOT)} (source: {source})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
