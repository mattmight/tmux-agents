# Use shell to resolve paths — GNU Make functions break on spaces in paths.
PROJECT_DIR := $(shell cd "$(dir $(lastword $(MAKEFILE_LIST)))" && pwd)
VENV := $(PROJECT_DIR)/.venv
BIN_DIR := $(HOME)/bin
SCRIPT := $(PROJECT_DIR)/bin/tmux-agents

.PHONY: install uninstall venv dev test lint format clean

install: venv
	@mkdir -p "$(BIN_DIR)"
	@ln -sf "$(SCRIPT)" "$(BIN_DIR)/tmux-agents"
	@echo "Installed: $(BIN_DIR)/tmux-agents -> $(SCRIPT)"

uninstall:
	@rm -f "$(BIN_DIR)/tmux-agents"
	@echo "Removed: $(BIN_DIR)/tmux-agents"

venv:
	@if [ ! -d "$(VENV)" ]; then \
		uv venv "$(VENV)"; \
	fi
	@uv pip install -q -e "$(PROJECT_DIR)[dev]"

dev: venv

test: venv
	@"$(VENV)/bin/pytest" -v

lint: venv
	@"$(VENV)/bin/ruff" check src/ tests/
	@"$(VENV)/bin/ruff" format --check src/ tests/

format: venv
	@"$(VENV)/bin/ruff" check --fix src/ tests/
	@"$(VENV)/bin/ruff" format src/ tests/

clean:
	rm -rf "$(VENV)" dist/ build/ src/*.egg-info
