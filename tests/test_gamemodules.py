"""Game modules: discovery, precedence, detection and what they reconfigure."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from gamehail import gamemodules


def write(directory: Path, name: str, body: str) -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"{name}.toml"
    path.write_text(body)
    return path


def test_bundled_modules_load():
    modules = gamemodules.discover()
    assert "star-citizen" in modules and "generic" in modules
    sc = modules["star-citizen"]
    assert "StarCitizen.exe" in sc.detect
    assert "mcp__scmcp" in sc.allowed_tools
    assert "laranite" in sc.vocabulary
    assert "scmcp" in sc.mcp


def test_user_module_overrides_a_bundled_one(tmp_path, monkeypatch):
    monkeypatch.setattr(gamemodules, "user_dir", lambda: tmp_path)
    write(tmp_path, "star-citizen", '''
[game]
id = "star-citizen"
name = "Star Citizen (mine)"
detect = ["StarCitizen.exe"]
''')
    assert gamemodules.discover()["star-citizen"].name == "Star Citizen (mine)"


def test_detection_matches_a_running_process(tmp_path, monkeypatch):
    monkeypatch.setattr(gamemodules, "user_dir", lambda: tmp_path)
    write(tmp_path, "othergame", '''
[game]
id = "othergame"
name = "Other Game"
detect = ["OtherGame.exe"]
''')
    modules = gamemodules.discover()
    found = gamemodules.detect(modules, processes=["bash", "OtherGame.exe", "kwin_wayland"])
    assert found is not None and found.id == "othergame"
    assert gamemodules.detect(modules, processes=["bash"]) is None


def test_resolve_prefers_the_override_then_detection_then_default(tmp_path, monkeypatch):
    monkeypatch.setattr(gamemodules, "user_dir", lambda: tmp_path)
    monkeypatch.setattr(gamemodules, "running_processes", lambda: ["StarCitizen.exe"])
    assert gamemodules.resolve("generic", True, "generic").id == "generic"
    assert gamemodules.resolve("generic", True, None).id == "star-citizen"
    assert gamemodules.resolve("generic", False, None).id == "generic"


def test_inline_mcp_becomes_a_real_mcp_json(tmp_path, monkeypatch):
    monkeypatch.setattr(gamemodules, "user_dir", lambda: tmp_path)
    monkeypatch.setenv("HOME", "/home/tester")
    write(tmp_path, "withmcp", '''
[game]
id = "withmcp"
name = "With MCP"

[mcp.thing]
command = "node"
args = ["$HOME/src/thing/index.js"]
''')
    module = gamemodules.discover()["withmcp"]
    path = module.mcp_config_path(tmp_path / "work")
    data = json.loads(path.read_text())
    assert data["mcpServers"]["thing"]["args"] == ["/home/tester/src/thing/index.js"]


def test_a_broken_module_is_skipped_not_fatal(tmp_path, monkeypatch, caplog):
    monkeypatch.setattr(gamemodules, "user_dir", lambda: tmp_path)
    write(tmp_path, "broken", "this is not toml {{{")
    modules = gamemodules.discover()
    assert "broken" not in modules
    assert "generic" in modules, "one bad file must not hide the good ones"


def test_switching_module_reconfigures_the_pipeline(tmp_path, monkeypatch):
    from gamehail.config import Config
    from gamehail.pipeline import Pipeline

    monkeypatch.setattr(gamemodules, "running_processes", lambda: [])
    cfg = Config(work_dir=tmp_path)
    cfg.games.default = "generic"
    pipe = Pipeline(cfg)
    try:
        assert pipe.module.id == "generic"
        assert cfg.stt.vocabulary == []

        pipe.apply_module(gamemodules.discover()["star-citizen"])
        assert cfg.backend.allowed_tools == ["Read", "mcp__scmcp"]
        assert "laranite" in cfg.stt.vocabulary
        assert cfg.backend.mcp_config and cfg.backend.mcp_config.exists()
        assert "Star Citizen" in cfg.backend.system_prompt
    finally:
        pipe.close()
