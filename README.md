# ServerTUI

A lightweight terminal dashboard for managing local server infrastructure — Cloudflare tunnels, Docker containers, Ollama models, and system resources.

Built with [Textual](https://github.com/Textualize/textual) for a smooth, responsive TUI experience.

![Python 3.11+](https://img.shields.io/badge/python-3.11+-blue)
![License](https://img.shields.io/badge/license-MIT-green)

## Features

- **System Resources** — CPU, RAM, Swap, Disk, Network I/O, Load average, Uptime
- **Cloudflare Tunnels** — Status, start/stop/restart, journal logs viewer
- **Docker Containers** — Status, CPU/RAM stats, start/stop/restart
- **Ollama LLM** — Online status, loaded models (VRAM/RAM usage, expiry), installed models
- **Responsive Layout** — Side-by-side on wide terminals, stacked on narrow ones (< 90 cols)
- **Non-blocking** — Expensive data (Docker stats) fetched in background threads, UI stays snappy

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

## Keybindings

| Key | Action              |
|-----|---------------------|
| `s` | Start tunnel        |
| `t` | Stop tunnel         |
| `r` | Restart tunnel      |
| `l` | View tunnel logs    |
| `u` | Start container     |
| `d` | Stop container      |
| `x` | Restart container   |
| `f` | Force refresh       |
| `q` | Quit                |

## Refresh Intervals

| Data               | Interval | Method     |
|--------------------|----------|------------|
| System stats       | 2s       | Foreground |
| Tunnel status      | 2s       | Foreground |
| Docker stats       | 15s      | Background |
| Ollama status      | 15s      | Background |

Docker stats are fetched in a daemon thread to avoid blocking the UI (~1-2s per container).

## Configuration

Tunnels are configured in `app.py` via the `TUNNELS` list:

```python
TUNNELS = [
    {
        "name": "coloring",
        "service": "cloudflared-coloring",    # systemd user service name
        "domain": "coloring.cafe",
        "description": "Coloring Cafe",
    },
    # ...
]
```

Each tunnel maps to a `systemctl --user` service like `cloudflared-<name>.service`.

## License

MIT
