PLUGIN := dev.gamepilot.sdPlugin
PLUGINS_DIR := $(HOME)/.config/opendeck/plugins

.PHONY: test check icons deck-validate deck-install deck-package clean

test:
	uv run pytest -q

check: deck-validate test

icons:
	uv run python scripts/make_icons.py

deck-validate:
	python3 -c "import json,sys; json.load(open('$(PLUGIN)/manifest.json'))"
	python3 -m compileall -q $(PLUGIN)
	sh -n $(PLUGIN)/run.sh
	uv run python scripts/validate_plugin.py

# Copied rather than symlinked: OpenDeck resolves a plugin's property inspectors
# relative to the real directory and refuses paths that canonicalise outside it.
deck-install: deck-validate
	rm -rf $(PLUGINS_DIR)/$(PLUGIN)
	mkdir -p $(PLUGINS_DIR)
	cp -r $(PLUGIN) $(PLUGINS_DIR)/$(PLUGIN)
	@echo "installed to $(PLUGINS_DIR)/$(PLUGIN) — restart OpenDeck to pick it up"

deck-package:
	cd $(dir $(PLUGIN)) && zip -qr gamepilot-plugin.zip $(PLUGIN) -x '*/plugin.log' '*/__pycache__/*'
	@echo "wrote gamepilot-plugin.zip"

clean:
	find . -name __pycache__ -prune -exec rm -rf {} + 2>/dev/null || true
	rm -f $(PLUGIN)/plugin.log
