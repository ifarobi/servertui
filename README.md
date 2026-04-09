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

```bash
git clone git@github.com:ifarobi/servertui.git
cd servertui
python3 -m venv .venv
source .venv/bin/activate
pip install textual psutil docker
```

### Quick access (optional)

```bash
ln -sf "$(pwd)/run.sh" ~/.local/bin/servertui
```

Then just run `servertui` from anywhere.

## Usage

```bash
./run.sh
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

An example config is included in the repo as `apps.example.json`.

### Clone directory

Repos are cloned to `~/servertui/apps/<name>/`. Override with the `SERVERTUI_APPS_DIR` environment variable:

```bash
SERVERTUI_APPS_DIR=/opt/servertui/apps ./run.sh
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

## License

MIT
