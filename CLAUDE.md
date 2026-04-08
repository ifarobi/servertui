# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Overview

ServerTUI is a single-file Textual TUI (`app.py`, ~770 lines) for managing local server infrastructure: Cloudflare tunnels (via `systemctl --user`), Docker containers, Ollama models, and system stats.

## Run

```bash
./run.sh        # activates .venv and runs app.py
```

Dependencies: `textual psutil docker` on Python 3.11+. No tests, no linter, no build step.

## Architecture

Everything lives in `app.py`. Key pieces:

- **`DataStore`** (line 119): central mutable cache shared across panels. Holds system/tunnel/docker/ollama state. Panels read from it; background threads write to it.
- **Background fetchers**: `bg_fetch_expensive()` (Docker stats, ~1-2s/container, 15s interval) and `bg_fetch_cheap()` (Ollama, 15s) run in daemon threads so the UI never blocks. Cheap data (system stats, tunnel status) is polled on the Textual interval (2s) in the foreground.
- **Panels** (`SystemPanel`, `TunnelPanel`, `DockerPanel`, `OllamaPanel`): each `Static` widget renders from `DataStore` on tick. Layout is responsive — side-by-side ≥90 cols, stacked below — handled in `ServerTUI.on_resize`.
- **Modal screens**: `SelectorScreen` (pick a tunnel/container for an action) and `LogScreen` (journalctl viewer for tunnels).
- **`ServerTUI`** (line 509): app entrypoint, key bindings, action dispatch.

Shell-out helpers `run_cmd()` and `systemctl_user()` wrap subprocess with timeouts. Tunnels are configured by editing the `TUNNELS` list near the top of `app.py` — each entry maps to a `cloudflared-<name>.service` user unit.

## Keybindings

`1-5` switch tab · `s/t/r/l` tunnel start/stop/restart/logs · `u/d/x` container start/stop/restart · `g` timer logs · `R/E/L` app rebuild/edit-env/logs · `f` force refresh · `q` quit
