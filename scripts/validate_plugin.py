"""Check the deck plugin's manifest against the files it points at.

OpenDeck fails quietly on a broken manifest - the plugin loads and its keys do nothing
- so every path in it is checked here instead.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

PLUGIN = Path(__file__).resolve().parent.parent / "dev.gamepilot.sdPlugin"


def main() -> int:
    manifest = json.loads((PLUGIN / "manifest.json").read_text())
    problems: list[str] = []

    def check_image(value: str, where: str) -> None:
        # Icons are referenced without their extension; @2x is optional but expected.
        if not (PLUGIN / f"{value}.png").is_file():
            problems.append(f"{where}: missing {value}.png")
        elif not (PLUGIN / f"{value}@2x.png").is_file():
            problems.append(f"{where}: missing {value}@2x.png")

    for key in ("Name", "Version", "SDKVersion", "CodePath", "Actions"):
        if key not in manifest:
            problems.append(f"manifest: missing {key}")

    code_path = PLUGIN / manifest.get("CodePath", "")
    if not code_path.is_file():
        problems.append(f"manifest: CodePath {manifest.get('CodePath')} does not exist")
    elif not code_path.stat().st_mode & 0o111:
        problems.append(f"manifest: CodePath {manifest.get('CodePath')} is not executable")

    check_image(manifest.get("Icon", ""), "manifest")

    seen = set()
    for action in manifest.get("Actions", []):
        uuid = action.get("UUID", "<no uuid>")
        if uuid in seen:
            problems.append(f"{uuid}: duplicate UUID")
        seen.add(uuid)
        if not uuid.startswith("dev.gamepilot."):
            problems.append(f"{uuid}: UUID outside the plugin namespace")
        check_image(action.get("Icon", ""), uuid)
        for index, state in enumerate(action.get("States", [])):
            if "Image" in state:
                check_image(state["Image"], f"{uuid} state {index}")
        inspector = action.get("PropertyInspectorPath")
        if inspector and not (PLUGIN / inspector).is_file():
            problems.append(f"{uuid}: missing property inspector {inspector}")

    for problem in problems:
        print(problem, file=sys.stderr)
    print(f"{len(manifest.get('Actions', []))} actions checked, {len(problems)} problems")
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
