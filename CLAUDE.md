# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

See also: [AGENTS.md](AGENTS.md) for MCP server tools and workflows.

## Overview

ServerTUI is a Textual TUI for managing local server infrastructure: Cloudflare tunnels (via `systemctl --user`), Docker containers, git-managed app deployments, Ollama models, and system stats.

## Run

```bash
servertui            # installed entry point (uv tool install servertui)
./run.sh             # dev from repo checkout (uses `uv run servertui`)
```

Subcommands: `servertui tui` (default), `servertui mcp`, `servertui init`. Dependencies: `textual psutil docker mcp` on Python 3.11+. Build backend: hatchling.

## Architecture

All code lives in the `src/servertui/` package. Shared logic is in `core.py`; the TUI in `tui.py`; the MCP server in `mcp.py`; the CLI router in `cli.py`; `servertui init` in `init.py`. Key pieces:

- **`DataStore`** (line 135): central mutable cache shared across panels. Holds system/tunnel/docker/ollama state. Panels read from it; background threads write to it.
- **Background fetchers**: `bg_fetch_expensive()` (Docker stats, ~1-2s/container, 15s interval) and `bg_fetch_cheap()` (Ollama, 15s) run in daemon threads so the UI never blocks. Cheap data (system stats, tunnel status) is polled on the Textual interval (2s) in the foreground.
- **Panels** (`SystemPanel`, `TunnelPanel`, `DockerPanel`, `OllamaPanel`): each `Static` widget renders from `DataStore` on tick. Layout is responsive — side-by-side ≥90 cols, stacked below — handled in `ServerTUI.on_resize`.
- **Modal screens**: `SelectorScreen` (pick a tunnel/container for an action) and `LogScreen` (journalctl viewer for tunnels).
- **`ServerTUI`** (line 790): app entrypoint, key bindings, action dispatch.

Shell-out helpers `run_cmd()` and `systemctl_user()` wrap subprocess with timeouts. Tunnels are auto-discovered from `cloudflared-*.service` user units. Apps are configured via `~/.config/servertui/apps.json` with `git_url` fields — repos are auto-cloned into `~/servertui/apps/<name>/` (override via `SERVERTUI_APPS_DIR` env var).

## Keybindings

`1-5` switch tab · `s/t/r/l` tunnel start/stop/restart/logs · `u/d/x` container start/stop/restart · `g` timer logs · `R/E/L` app rebuild/edit-env/logs · `f` force refresh · `q` quit
