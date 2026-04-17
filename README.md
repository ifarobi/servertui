# ServerTUI

A lightweight terminal dashboard for managing local server infrastructure — Cloudflare tunnels, Docker containers, app deployments, Ollama models, and system resources.

Built with [Textual](https://github.com/Textualize/textual) for a smooth, responsive TUI experience.

![Python 3.11+](https://img.shields.io/badge/python-3.11+-blue)
![License](https://img.shields.io/badge/license-MIT-green)

## Features

- **System Resources** — CPU, RAM, Swap, Disk, Network I/O, Load average, Uptime
- **Cloudflare Tunnels** — Auto-discovered from systemd user units, start/stop/restart, journal log viewer
- **Docker Containers** — Status, CPU/RAM stats, start/stop/restart
- **Apps** — Git-managed app deployments with auto-clone, rebuild (pull + build), env file management, and tunnel cross-referencing
- **Ollama LLM** — Online status, loaded models (VRAM/RAM usage, expiry), installed models
- **Responsive Layout** — Side-by-side on wide terminals, stacked on narrow ones (< 90 cols)
- **Non-blocking** — Expensive data (Docker stats, git clone) fetched in background threads, UI stays snappy

## Architecture

```
┌──────────────────┬─────────────────────────────────┐
│                  │                                 │
│  System          │  Cloudflare Tunnels             │
│  Resources       │  ● coloring.cafe    running     │
│                  │  ● noko.ifarobi.com running     │
│  CPU  ████████░  │  ● ollage.ifarobi.  running     │
│  RAM  ███░░░░░░  │                                 │
│  Swap ░░░░░░░░░  │  Docker Containers              │
│  Disk █████░░░░  │  ● coloring-cafe    running     │
│                  │  ● plex             running     │
│  Net ↑ 1.2 GB   │  ● jellyfin         running     │
│  Net ↓ 8.4 GB   │                                 │
│                  │  Ollama LLM                     │
│                  │  ● Online v0.20.2               │
│                  │  💾 gemma4:e4b  8B  Q4_K_M      │
└──────────────────┴─────────────────────────────────┘
```

## Requirements

- Python 3.11+
- `systemctl --user` (for tunnel management)
- Docker daemon running (for container stats)
- Ollama running on `localhost:11434` (optional)

## Installation

### One-liner (recommended)

```bash
curl -fsSL https://raw.githubusercontent.com/ifarobi/servertui/main/install.sh | sh
```

This installs [uv](https://docs.astral.sh/uv/) if missing, then runs `uv tool install --upgrade servertui`. Safe to re-run — upgrades in place.

### With an existing Python tool manager

```bash
uv tool install servertui      # or: pipx install servertui
```

### First-time setup

```bash
servertui init                 # scaffolds ~/.config/servertui/
$EDITOR ~/.config/servertui/apps.json
servertui                      # launches the TUI
```

## Development

```bash
git clone git@github.com:ifarobi/servertui.git
cd servertui
./run.sh                       # uses `uv run servertui` from the repo
```

## App Configuration

Apps are configured in `~/.config/servertui/apps.json`. ServerTUI auto-clones repos into a managed directory (`~/servertui/apps/` by default) and handles the full deploy lifecycle: git pull, docker build, and container restart.

```json
[
  {
    "name": "myapp",
    "git_url": "git@github.com:user/myapp.git",
    "tunnel": "myapp"
  },
  {
    "name": "myapp-staging",
    "git_url": "git@github.com:user/myapp.git",
    "tunnel": "myapp",
    "branch": "staging",
    "compose_file": "docker-compose.staging.yml"
  }
]
```

| Field | Required | Description |
|-------|----------|-------------|
| `name` | yes | Display name. Container will be `servertui-<name>` |
| `git_url` | yes | Git remote URL (SSH or HTTPS) |
| `tunnel` | no | Cloudflare tunnel name to cross-reference for status display |
| `branch` | no | Branch to clone/checkout. Defaults to repo's default branch |
| `compose_file` | no | Override compose filename (default: `compose.yml` or `docker-compose.yml`) |

An example config is bundled with the package — run `servertui init` to copy it into place, or view it on [GitHub](https://github.com/ifarobi/servertui/blob/main/src/servertui/apps.example.json).

### Clone directory

Repos are cloned to `~/servertui/apps/<name>/`. Override with the `SERVERTUI_APPS_DIR` environment variable:

```bash
SERVERTUI_APPS_DIR=/opt/servertui/apps servertui
```

### Env files

Each app can have a dotenv file at `~/.config/servertui/env/<name>.env`. ServerTUI creates these with `600` permissions and warns if they're more permissive.

### Build modes

ServerTUI auto-detects how to build each app:

- **compose** — if `compose.yml` or `docker-compose.yml` exists (or `compose_file` is set): runs `docker compose up -d --build`
- **dockerfile** — if only a `Dockerfile` exists: runs `docker build` + `docker run`

## Tunnel Configuration

Tunnels are auto-discovered from `cloudflared-*.service` systemd user units — no configuration needed. Each unit like `cloudflared-mysite.service` is automatically picked up and shown in the Tunnels panel.

## Keybindings

| Key | Action |
|-----|--------|
| `1-5` | Switch tab |
| `s` | Start tunnel |
| `t` | Stop tunnel |
| `r` | Restart tunnel |
| `l` | View tunnel logs |
| `u` | Start container |
| `d` | Stop container |
| `x` | Restart container |
| `R` | Rebuild app (git pull + build) |
| `E` | Edit app env file |
| `L` | View app logs |
| `g` | View timer logs |
| `f` | Force refresh |
| `q` | Quit |

## Refresh Intervals

| Data | Interval | Method |
|------|----------|--------|
| System stats | 2s | Foreground |
| Tunnel status | 2s | Foreground |
| App status | 2s | Foreground |
| Docker stats | 15s | Background |
| Ollama status | 15s | Background |

Docker stats are fetched in a daemon thread to avoid blocking the UI (~1-2s per container).

## MCP Server (AI Agent Integration)

ServerTUI includes an MCP server that lets AI agents (like Claude Code) query
status, read logs, manage Docker containers, and trigger app rebuilds.

### Setup

1. Add to your Claude Code MCP config (`~/.claude/settings.json` or project `.claude/settings.json`):

   ```json
   {
     "mcpServers": {
       "servertui": {
         "command": "servertui",
         "args": ["mcp"]
       }
     }
   }
   ```

2. Restart Claude Code. The tools will be available automatically.

### Available Tools

| Tool | Description |
|------|-------------|
| `get_docker_containers` | List containers with status, CPU%, RAM |
| `get_app_status` | List apps with container/git/build/env status |
| `get_app_logs` | Tail docker logs for an app |
| `get_container_logs` | Tail docker logs for any container |
| `rebuild_app` | Trigger git pull + build (async, returns job ID) |
| `get_rebuild_status` | Check rebuild progress/completion |
| `docker_start` | Start a stopped container |
| `docker_stop` | Stop a running container |
| `docker_restart` | Restart a container |

### Example Conversation

> **You:** "Is coloring-cafe running?"
> Claude Code calls `get_app_status(name="coloring-cafe")` and reports the result.

> **You:** "Redeploy it"
> Claude Code calls `rebuild_app(name="coloring-cafe")`, gets a job ID,
> then polls `get_rebuild_status` until it completes.

> **You:** "Show me the last 50 lines of logs"
> Claude Code calls `get_app_logs(name="coloring-cafe", lines=50)`.

## License

MIT
