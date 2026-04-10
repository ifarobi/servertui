# ServerTUI MCP Server — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Expose ServerTUI's app and Docker operations as MCP tools so Claude Code can query state, read logs, manage containers, and trigger rebuilds.

**Architecture:** Extract shared logic from `app.py` into `core.py` (config, helpers, docker ops, app ops, rebuild). Build `mcp_server.py` on top of `core.py` using the `mcp` Python SDK's `FastMCP`. Update `app.py` to import from `core.py`. Add README docs.

**Tech Stack:** Python 3.11+, `mcp` (FastMCP), `docker`, `psutil` (existing)

---

## File Structure

| File | Action | Responsibility |
|------|--------|---------------|
| `core.py` | Create | Config, data types, helpers, docker ops, app ops, rebuild generator |
| `mcp_server.py` | Create | MCP tool definitions, rebuild job tracking, server entry point |
| `app.py` | Modify | Replace inline definitions with `from core import ...` |
| `README.md` | Modify | Add MCP Server section |

---

### Task 1: Extract core.py from app.py

**Files:**
- Create: `core.py`
- Modify: `app.py`

This is a pure extraction — move code out of `app.py` into `core.py`, then replace the moved code in `app.py` with imports. No behavior change.

- [ ] **Step 1: Create `core.py` with config, data types, and helpers**

Create `core.py` with everything that both the TUI and MCP server need. This is code copied verbatim from `app.py` lines 1-307 (imports through `git_state`) plus `AppInfo` (lines 156-166), `inspect_env_file` (lines 240-261), `detect_build_mode` (lines 264-275), and `fmt_bytes` (lines 218-225).

```python
"""
ServerTUI core — shared logic for TUI and MCP server.
Config, data types, helpers, Docker operations, app status, rebuild.
"""

import json
import os
import re
import shlex
import subprocess
import sys
from dataclasses import dataclass, asdict
from datetime import datetime
from pathlib import Path
from threading import Lock

import docker

# ─── Config ──────────────────────────────────────────────────────────

CONFIG_DIR = Path.home() / ".config" / "servertui"
APPS_CONFIG = CONFIG_DIR / "apps.json"
ENV_DIR = CONFIG_DIR / "env"
APPS_DIR = Path(os.environ.get("SERVERTUI_APPS_DIR", str(Path.home() / "servertui" / "apps")))


@dataclass(frozen=True)
class App:
    name: str
    git_url: str
    tunnel: str | None = None
    branch: str | None = None
    compose_file: str | None = None

    @property
    def repo_path(self) -> Path:
        return APPS_DIR / self.name


def load_apps() -> list[App]:
    """Load apps from ~/.config/servertui/apps.json. Missing file -> []."""
    if not APPS_CONFIG.exists():
        return []
    try:
        raw = json.loads(APPS_CONFIG.read_text())
    except (OSError, json.JSONDecodeError) as e:
        print(f"[servertui] failed to read {APPS_CONFIG}: {e}", file=sys.stderr)
        return []
    if not isinstance(raw, list):
        print(f"[servertui] {APPS_CONFIG}: expected a JSON array", file=sys.stderr)
        return []
    out: list[App] = []
    for i, entry in enumerate(raw):
        if not isinstance(entry, dict):
            print(f"[servertui] {APPS_CONFIG}[{i}]: not an object, skipping", file=sys.stderr)
            continue
        name = entry.get("name")
        git_url = entry.get("git_url")
        if not isinstance(name, str) or not isinstance(git_url, str):
            print(f"[servertui] {APPS_CONFIG}[{i}]: missing 'name' or 'git_url', skipping",
                  file=sys.stderr)
            continue
        tunnel = entry.get("tunnel")
        if tunnel is not None and not isinstance(tunnel, str):
            print(f"[servertui] {APPS_CONFIG}[{i}]: 'tunnel' must be a string, skipping",
                  file=sys.stderr)
            continue
        branch = entry.get("branch")
        if branch is not None and not isinstance(branch, str):
            print(f"[servertui] {APPS_CONFIG}[{i}]: 'branch' must be a string, skipping",
                  file=sys.stderr)
            continue
        compose_file = entry.get("compose_file")
        if compose_file is not None and not isinstance(compose_file, str):
            print(f"[servertui] {APPS_CONFIG}[{i}]: 'compose_file' must be a string, skipping",
                  file=sys.stderr)
            continue
        out.append(App(
            name=name,
            git_url=git_url,
            tunnel=tunnel,
            branch=branch,
            compose_file=compose_file,
        ))
    return out


# ─── Clone management ────────────────────────────────────────────────

_clone_status: dict[str, str] = {}
_clone_lock = Lock()


def clone_if_missing(app: App) -> None:
    """Clone app repo if not already present. Updates _clone_status."""
    if app.repo_path.exists():
        with _clone_lock:
            _clone_status[app.name] = "done"
        return
    APPS_DIR.mkdir(parents=True, exist_ok=True)
    with _clone_lock:
        _clone_status[app.name] = "cloning"
    cmd = ["git", "clone", app.git_url, str(app.repo_path)]
    if app.branch:
        cmd.extend(["--branch", app.branch])
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
        with _clone_lock:
            if result.returncode == 0:
                _clone_status[app.name] = "done"
            else:
                _clone_status[app.name] = result.stderr.strip() or "clone failed"
    except subprocess.TimeoutExpired:
        with _clone_lock:
            _clone_status[app.name] = "clone timed out"
    except OSError as e:
        with _clone_lock:
            _clone_status[app.name] = str(e)


def get_clone_status(app_name: str) -> str:
    """Return clone status for an app: 'cloning' | 'done' | error string."""
    with _clone_lock:
        return _clone_status.get(app_name, "done")


# ─── Helpers ─────────────────────────────────────────────────────────

def run_cmd(cmd: str, timeout: int = 5) -> str:
    try:
        result = subprocess.run(
            shlex.split(cmd), capture_output=True, text=True, timeout=timeout,
        )
        return result.stdout.strip()
    except Exception as e:
        return f"error: {e}"


def fmt_bytes(n: int | float) -> str:
    if n < 0:
        return "N/A"
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if abs(n) < 1024:
            return f"{n:.1f} {unit}"
        n /= 1024
    return f"{n:.1f} PB"


def inspect_env_file(path: Path) -> tuple[int | None, bool]:
    """Return (key_count, perms_ok). key_count is None if file missing.
    perms_ok is True when the file is missing OR its mode is exactly 0o600."""
    try:
        st = path.stat()
    except FileNotFoundError:
        return (None, True)
    except OSError:
        return (None, False)
    perms_ok = (st.st_mode & 0o777) == 0o600
    try:
        count = 0
        with path.open("r", encoding="utf-8", errors="replace") as f:
            for line in f:
                s = line.strip()
                if not s or s.startswith("#"):
                    continue
                if "=" in s:
                    count += 1
        return (count, perms_ok)
    except OSError:
        return (None, perms_ok)


def detect_build_mode(repo_path: Path, compose_file: str | None = None) -> str:
    """Return 'compose', 'dockerfile', or 'none'."""
    if not repo_path.is_dir():
        return "none"
    if compose_file:
        return "compose" if (repo_path / compose_file).exists() else "none"
    if (repo_path / "compose.yml").exists() or (repo_path / "docker-compose.yml").exists():
        return "compose"
    if (repo_path / "Dockerfile").exists():
        return "dockerfile"
    return "none"


def git_state(repo_path: Path) -> str:
    """Cheap best-effort git state: 'clean' / 'dirty' / 'behind N' / '?'."""
    if not (repo_path / ".git").exists():
        return "?"
    try:
        porcelain = subprocess.run(
            ["git", "-C", str(repo_path), "status", "--porcelain"],
            capture_output=True, text=True, timeout=3,
        )
        if porcelain.returncode != 0:
            return "?"
        dirty = bool(porcelain.stdout.strip())
        behind = subprocess.run(
            ["git", "-C", str(repo_path), "rev-list", "--count", "HEAD..@{u}"],
            capture_output=True, text=True, timeout=3,
        )
        n_behind = 0
        if behind.returncode == 0:
            try:
                n_behind = int(behind.stdout.strip() or "0")
            except ValueError:
                n_behind = 0
        if dirty:
            return "dirty"
        if n_behind > 0:
            return f"behind {n_behind}"
        return "clean"
    except (subprocess.TimeoutExpired, OSError):
        return "?"


# ─── App status ──────────────────────────────────────────────────────

@dataclass
class AppInfo:
    name: str
    container_status: str
    image: str | None
    uptime: str | None
    tunnel: str | None
    tunnel_status: str | None
    git_state: str
    env_key_count: int | None
    env_perms_ok: bool
    build_mode: str

    def to_dict(self) -> dict:
        return asdict(self)


def fetch_app_status(apps: list[App], tunnel_status_by_service: dict[str, str] | None = None) -> list[AppInfo]:
    """Snapshot state of every configured app. Cheap: stat + 2 git + 1 docker inspect per app."""
    if tunnel_status_by_service is None:
        tunnel_status_by_service = {}

    try:
        client = docker.from_env()
        docker_ok = True
    except Exception:
        client = None
        docker_ok = False

    out: list[AppInfo] = []
    for app in apps:
        container_name = f"servertui-{app.name}"
        status = "missing"
        image = None
        uptime = None
        if docker_ok:
            try:
                c = client.containers.get(container_name)
                status = "running" if c.status == "running" else "stopped"
                image = (c.image.tags[0] if c.image.tags else c.image.short_id)
                started = c.attrs.get("State", {}).get("StartedAt", "")
                if started and status == "running":
                    try:
                        dt = datetime.fromisoformat(started.replace("Z", "+00:00"))
                        delta = datetime.now(dt.tzinfo) - dt
                        secs = int(delta.total_seconds())
                        if secs < 60:
                            uptime = f"{secs}s"
                        elif secs < 3600:
                            uptime = f"{secs // 60}m"
                        elif secs < 86400:
                            uptime = f"{secs // 3600}h"
                        else:
                            uptime = f"{secs // 86400}d"
                    except Exception:
                        uptime = None
            except docker.errors.NotFound:
                status = "missing"
            except Exception:
                status = "missing"

        env_path = ENV_DIR / f"{app.name}.env"
        env_count, env_perms_ok = inspect_env_file(env_path)

        tunnel_service = (
            f"cloudflared-{app.tunnel}" if app.tunnel else None
        )
        tunnel_status = (
            tunnel_status_by_service.get(tunnel_service) if tunnel_service else None
        )

        cs = get_clone_status(app.name)
        if cs == "cloning":
            app_git_state = "cloning"
            app_build_mode = "none"
        elif cs != "done":
            app_git_state = "clone-failed"
            app_build_mode = "none"
        else:
            app_git_state = git_state(app.repo_path)
            app_build_mode = detect_build_mode(app.repo_path, app.compose_file)

        out.append(AppInfo(
            name=app.name,
            container_status=status,
            image=image,
            uptime=uptime,
            tunnel=app.tunnel,
            tunnel_status=tunnel_status,
            git_state=app_git_state,
            env_key_count=env_count,
            env_perms_ok=env_perms_ok,
            build_mode=app_build_mode,
        ))

    return out


# ─── Docker operations ───────────────────────────────────────────────

def docker_container_list() -> list[dict]:
    """Return containers with name, status, image (cheap, no stats)."""
    try:
        client = docker.from_env()
        client.ping()
    except Exception:
        return []
    containers = []
    for c in client.containers.list(all=True):
        containers.append({
            "name": c.name,
            "status": c.status,
            "image": c.image.tags[0] if c.image.tags else c.short_id,
        })
    containers.sort(key=lambda c: (c["status"] != "running", c["name"]))
    return containers


def docker_container_stats() -> list[dict]:
    """Return containers with name, status, image, cpu_pct, mem_usage, mem_limit (expensive)."""
    try:
        client = docker.from_env()
        client.ping()
    except Exception:
        return []
    containers = []
    for c in client.containers.list(all=True):
        entry = {
            "name": c.name,
            "status": c.status,
            "image": c.image.tags[0] if c.image.tags else c.short_id,
            "cpu_pct": 0.0,
            "mem_usage": 0,
            "mem_limit": 0,
        }
        if c.status == "running":
            try:
                stats = c.stats(stream=False)
                cpu_delta = (
                    stats["cpu_stats"]["cpu_usage"]["total_usage"]
                    - stats["precpu_stats"]["cpu_usage"]["total_usage"]
                )
                sys_delta = (
                    stats["cpu_stats"]["system_cpu_usage"]
                    - stats["precpu_stats"]["system_cpu_usage"]
                )
                n_cpus = stats["cpu_stats"].get("online_cpus", 1)
                entry["cpu_pct"] = (
                    (cpu_delta / sys_delta) * n_cpus * 100
                    if sys_delta > 0 else 0
                )
                entry["mem_usage"] = stats["memory_stats"].get("usage", 0)
                entry["mem_limit"] = stats["memory_stats"].get("limit", 0)
            except Exception:
                pass
        containers.append(entry)
    containers.sort(key=lambda c: (c["status"] != "running", c["name"]))
    return containers


def docker_action(name: str, action: str) -> str:
    """Start/stop/restart a container by name. Returns success or error message."""
    if action not in ("start", "stop", "restart"):
        return f"Unknown action: {action}"
    try:
        client = docker.from_env()
        container = client.containers.get(name)
        getattr(container, action)()
        return f"{action.capitalize()}ed {name}"
    except docker.errors.NotFound:
        return f"Container not found: {name}"
    except Exception as e:
        return f"Error: {e}"


# ─── Rebuild ─────────────────────────────────────────────────────────

def rebuild_app(app: App):
    """Generator that yields output lines from git pull + build + restart.
    Final yielded value is a string '[exit 0]' or '[exit N]'."""
    container = f"servertui-{app.name}"
    env_path = ENV_DIR / f"{app.name}.env"

    cs = get_clone_status(app.name)
    if cs == "cloning":
        yield "[yellow]repo is still cloning, please wait...[/]"
        yield "[exit 1]"
        return
    if not app.repo_path.is_dir():
        yield f"[red]repo not cloned yet: {app.repo_path}[/]"
        if cs != "done":
            yield f"[red]clone error: {cs}[/]"
        yield "[exit 1]"
        return

    mode = detect_build_mode(app.repo_path, app.compose_file)
    if mode == "none":
        if app.compose_file:
            yield f"[red]compose_file not found: {app.repo_path / app.compose_file}[/]"
        else:
            yield "[red]no Dockerfile or compose.yml in repo[/]"
        yield "[exit 1]"
        return

    if env_path.exists():
        st = env_path.stat()
        if (st.st_mode & 0o777) != 0o600:
            yield f"[red]env file perms looser than 600: {env_path}[/]"
            yield "[red]fix with: chmod 600 {}[/]".format(env_path)
            yield "[exit 1]"
            return

    def stream(cmd: list[str], cwd: Path | None = None):
        yield f"[dim]$ {' '.join(shlex.quote(c) for c in cmd)}[/]"
        try:
            proc = subprocess.Popen(
                cmd, cwd=str(cwd) if cwd else None,
                stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                text=True, bufsize=1,
            )
        except OSError as e:
            yield f"[red]failed to spawn: {e}[/]"
            yield 1
            return
        assert proc.stdout is not None
        for line in proc.stdout:
            yield line.rstrip()
        proc.wait()
        yield proc.returncode

    # 1. git pull --ff-only
    rc = None
    for item in stream(["git", "pull", "--ff-only"], cwd=app.repo_path):
        if isinstance(item, int):
            rc = item
        else:
            yield item
    if rc != 0:
        yield "[red]git pull failed -- aborting[/]"
        yield f"[exit {rc}]"
        return

    # 2. Build + restart
    if mode == "dockerfile":
        image_tag = f"servertui-{app.name}"
        rc = None
        for item in stream(["docker", "build", "-t", image_tag, "."], cwd=app.repo_path):
            if isinstance(item, int):
                rc = item
            else:
                yield item
        if rc != 0:
            yield "[red]docker build failed -- existing container untouched[/]"
            yield f"[exit {rc}]"
            return

        yield "[dim]$ docker stop {} && docker rm -f {}[/]".format(container, container)
        subprocess.run(["docker", "stop", container],
                       capture_output=True, text=True, timeout=30)
        subprocess.run(["docker", "rm", "-f", container],
                       capture_output=True, text=True, timeout=30)
        check = subprocess.run(
            ["docker", "container", "inspect", container],
            capture_output=True, text=True,
        )
        if check.returncode == 0:
            yield f"[red]failed to remove existing container {container}[/]"
            yield "[red]try manually: docker rm -f {}[/]".format(container)
            yield "[exit 1]"
            return

        run_cmd_list = [
            "docker", "run", "-d",
            "--name", container,
            "--restart", "unless-stopped",
        ]
        if env_path.exists():
            run_cmd_list += ["--env-file", str(env_path)]
        run_cmd_list.append(image_tag)

        rc = None
        for item in stream(run_cmd_list):
            if isinstance(item, int):
                rc = item
            else:
                yield item
        yield f"[exit {rc}]"
        return

    # compose mode
    if app.compose_file:
        compose_file = app.repo_path / app.compose_file
    else:
        compose_file = app.repo_path / "compose.yml"
        if not compose_file.exists():
            compose_file = app.repo_path / "docker-compose.yml"
    if env_path.exists():
        yield (
            "[yellow]note: compose mode -- ServerTUI's env file is NOT auto-wired.[/]\n"
            "[yellow]Reference it in compose.yml via `env_file: "
            f"{env_path}` or `${{VAR}}` interpolation.[/]"
        )
    cmd = ["docker", "compose", "-f", str(compose_file), "up", "-d", "--build"]
    rc = None
    for item in stream(cmd, cwd=app.repo_path):
        if isinstance(item, int):
            rc = item
        else:
            yield item
    yield f"[exit {rc}]"
```

- [ ] **Step 2: Update `app.py` to import from `core.py`**

Replace the top of `app.py` (lines 1-118 covering imports, config, `App`, `load_apps`, clone logic) and the helper functions (lines 170-307 covering `run_cmd` through `git_state`), `AppInfo` (lines 155-166), `inspect_env_file` (lines 240-261), `detect_build_mode` (lines 264-275), `fmt_bytes` (lines 218-225), and `rebuild_app` (lines 633-766) with imports from `core`.

The new top of `app.py` should be:

```python
"""
ServerTUI — Local server dashboard for managing Cloudflare tunnels,
Docker containers, apps (local repos), and monitoring system resources.
"""

import os
import re
import shlex
import socket
import subprocess
import json
import sys
import urllib.request
import urllib.error
from datetime import datetime
from threading import Thread, Lock
from pathlib import Path

import psutil
from textual.app import App as TextualApp, ComposeResult
from textual.binding import Binding
from textual.containers import Container, Horizontal, Vertical
from textual.screen import ModalScreen
from textual.widgets import (
    DataTable,
    Footer,
    Header,
    RichLog,
    Static,
    TabbedContent,
    TabPane,
)

from core import (
    App as AppConfig,
    AppInfo,
    APPS_CONFIG,
    APPS_DIR,
    CONFIG_DIR,
    ENV_DIR,
    clone_if_missing,
    detect_build_mode,
    docker_container_stats,
    fetch_app_status,
    fmt_bytes,
    get_clone_status,
    git_state,
    inspect_env_file,
    load_apps,
    rebuild_app,
    run_cmd,
)

# ─── Config ──────────────────────────────────────────────────────────

TUNNELS: list[dict] = []

APPS: list[AppConfig] = load_apps()
```

Key changes:
- Import `App` as `AppConfig` to avoid collision with `textual.app.App`
- Remove all definitions now in `core.py`: `App`, `AppInfo`, `load_apps`, clone functions, `run_cmd`, `fmt_bytes`, `inspect_env_file`, `detect_build_mode`, `git_state`, `rebuild_app`, path constants
- Remove `from dataclasses import dataclass` (no longer needed in app.py for `App`/`AppInfo`)
- Keep: `TUNNELS`, `APPS` (as `list[AppConfig]`), `OLLAMA_BASE`, `ollama_api`, `systemctl_user`, `detect_tunnel_domain`, `get_uptime`, `bar`, `edit_env_file`, `DataStore`, all panels, all screens, `ServerTUI`
- In `DataStore.fetch_docker`, replace the inline docker logic with a call to `docker_container_stats()` from core
- In `DataStore.fetch_apps`, replace the inline logic with a call to `fetch_app_status(APPS, tunnel_status_by_service)` from core
- Replace all references to `App` type (in type hints for `rebuild_app`, `edit_env_file`, `BuildScreen`, etc.) with `AppConfig`

For `DataStore.fetch_docker`, the new body should be:

```python
def fetch_docker(self):
    """Fetch docker container list + stats (EXPENSIVE — runs in bg)."""
    result = docker_container_stats()
    self.set("docker", result if result else None)
```

For `DataStore.fetch_apps`, the new body should be:

```python
def fetch_apps(self):
    """Snapshot state of every configured app."""
    tunnels = self._data.get("tunnels") or []
    tunnel_status_by_service = {}
    for t in tunnels:
        state = t.get("ActiveState", "unknown")
        sub = t.get("SubState", "unknown")
        tunnel_service = t.get("service", "")
        tunnel_status_by_service[tunnel_service] = (
            "active" if (state == "active" and sub == "running") else "inactive"
        )
    self.set("apps", fetch_app_status(APPS, tunnel_status_by_service))
```

- [ ] **Step 3: Verify the TUI still works**

Run: `cd /home/ifr/sandbox/servertui && .venv/bin/python -c "from core import load_apps, App, AppInfo, rebuild_app, docker_container_list, docker_container_stats, docker_action, fetch_app_status; print('core imports OK')"`
Expected: `core imports OK`

Run: `cd /home/ifr/sandbox/servertui && .venv/bin/python -c "import app; print('app imports OK')"`
Expected: `app imports OK`

- [ ] **Step 4: Commit**

```bash
git add core.py app.py
git commit -m "Extract shared logic from app.py into core.py

Move config, data types, helpers, docker operations, app status,
and rebuild logic into core.py so both the TUI and MCP server
can share them."
```

---

### Task 2: Create MCP server with Docker read tools

**Files:**
- Create: `mcp_server.py`

- [ ] **Step 1: Create `mcp_server.py` with `get_docker_containers` and `get_container_logs`**

```python
"""
ServerTUI MCP Server — exposes app and Docker operations as MCP tools
for AI agent integration (e.g., Claude Code).
"""

import json
import re
import shlex
import subprocess
from dataclasses import dataclass
from threading import Lock, Thread
from uuid import uuid4

from mcp.server.fastmcp import FastMCP

from core import (
    App,
    AppInfo,
    docker_action,
    docker_container_list,
    docker_container_stats,
    fetch_app_status,
    fmt_bytes,
    load_apps,
    rebuild_app as core_rebuild_app,
)

mcp = FastMCP("servertui")


@mcp.tool()
def get_docker_containers(brief: bool = False) -> str:
    """List all Docker containers with status and image. Set brief=False (default) to include CPU% and RAM stats (slow, ~1-2s per container).

    Args:
        brief: If True, skip expensive CPU/RAM stats and return only name, status, image.
    """
    if brief:
        containers = docker_container_list()
    else:
        containers = docker_container_stats()
    if not containers:
        return json.dumps({"error": "Docker daemon not reachable or no containers found"})
    return json.dumps(containers, indent=2)


@mcp.tool()
def get_container_logs(name: str, lines: int = 100) -> str:
    """Get recent Docker logs for any container by name.

    Args:
        name: Container name.
        lines: Number of tail lines to return (default 100).
    """
    try:
        result = subprocess.run(
            ["docker", "logs", "--tail", str(lines), name],
            capture_output=True, text=True, timeout=10,
        )
        if result.returncode != 0:
            return f"Error: {result.stderr.strip()}"
        return result.stdout
    except subprocess.TimeoutExpired:
        return "Error: timed out reading logs"
    except Exception as e:
        return f"Error: {e}"


if __name__ == "__main__":
    mcp.run()
```

- [ ] **Step 2: Verify the MCP server starts**

Run: `cd /home/ifr/sandbox/servertui && echo '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2025-03-26","capabilities":{},"clientInfo":{"name":"test","version":"0.1"}}}' | .venv/bin/python mcp_server.py 2>/dev/null | head -1`
Expected: A JSON response containing `"serverInfo"` and `"servertui"`.

- [ ] **Step 3: Commit**

```bash
git add mcp_server.py
git commit -m "Add MCP server with Docker container tools

get_docker_containers (brief and full stats) and
get_container_logs for reading Docker state and logs."
```

---

### Task 3: Add app status and log tools to MCP server

**Files:**
- Modify: `mcp_server.py`

- [ ] **Step 1: Add `get_app_status` and `get_app_logs` tools**

Add these two tool functions to `mcp_server.py` before the `if __name__` block:

```python
@mcp.tool()
def get_app_status(name: str | None = None) -> str:
    """List configured apps with container status, git state, build mode, env info, and tunnel status.

    Args:
        name: Optional app name to filter to a single app.
    """
    apps = load_apps()
    if not apps:
        return json.dumps({"error": "No apps configured in ~/.config/servertui/apps.json"})
    if name:
        apps = [a for a in apps if a.name == name]
        if not apps:
            return json.dumps({"error": f"App not found: {name}"})
    statuses = fetch_app_status(apps)
    return json.dumps([s.to_dict() for s in statuses], indent=2)


@mcp.tool()
def get_app_logs(name: str, lines: int = 100) -> str:
    """Get recent Docker logs for an app's container (servertui-<name>).

    Args:
        name: App name (container will be servertui-<name>).
        lines: Number of tail lines to return (default 100).
    """
    container = f"servertui-{name}"
    return get_container_logs(container, lines)
```

- [ ] **Step 2: Verify both tools respond**

Run: `cd /home/ifr/sandbox/servertui && .venv/bin/python -c "from mcp_server import get_app_status, get_app_logs; print('tools import OK')"`
Expected: `tools import OK`

- [ ] **Step 3: Commit**

```bash
git add mcp_server.py
git commit -m "Add app status and log tools to MCP server

get_app_status returns app config + runtime state.
get_app_logs returns docker logs for servertui-<name> containers."
```

---

### Task 4: Add Docker write tools to MCP server

**Files:**
- Modify: `mcp_server.py`

- [ ] **Step 1: Add `docker_start`, `docker_stop`, `docker_restart` tools**

Add these three tool functions to `mcp_server.py` before the `if __name__` block:

```python
@mcp.tool()
def docker_start(name: str) -> str:
    """Start a stopped Docker container.

    Args:
        name: Container name.
    """
    return docker_action(name, "start")


@mcp.tool()
def docker_stop(name: str) -> str:
    """Stop a running Docker container.

    Args:
        name: Container name.
    """
    return docker_action(name, "stop")


@mcp.tool()
def docker_restart(name: str) -> str:
    """Restart a Docker container.

    Args:
        name: Container name.
    """
    return docker_action(name, "restart")
```

- [ ] **Step 2: Verify tools import**

Run: `cd /home/ifr/sandbox/servertui && .venv/bin/python -c "from mcp_server import docker_start, docker_stop, docker_restart; print('docker write tools OK')"`
Expected: `docker write tools OK`

- [ ] **Step 3: Commit**

```bash
git add mcp_server.py
git commit -m "Add Docker start/stop/restart tools to MCP server"
```

---

### Task 5: Add rebuild_app and get_rebuild_status tools

**Files:**
- Modify: `mcp_server.py`

- [ ] **Step 1: Add rebuild job tracking and tools**

Add the job tracking dataclass and lock near the top of `mcp_server.py` (after the imports, before the tool definitions):

```python
@dataclass
class RebuildJob:
    job_id: str
    app_name: str
    status: str             # "running" | "done" | "failed"
    output_lines: list[str]
    exit_code: int | None


_jobs: dict[str, RebuildJob] = {}
_jobs_lock = Lock()


def _run_rebuild(job: RebuildJob, app: App) -> None:
    """Background thread: consume rebuild_app generator and update job state."""
    try:
        for line in core_rebuild_app(app):
            with _jobs_lock:
                job.output_lines.append(line)
    finally:
        with _jobs_lock:
            # Parse exit code from final line like "[exit 0]" or "[exit 1]"
            exit_code = 1
            for line in reversed(job.output_lines):
                m = re.match(r"\[exit (\d+)\]", line)
                if m:
                    exit_code = int(m.group(1))
                    break
            job.exit_code = exit_code
            job.status = "done" if exit_code == 0 else "failed"
```

Add these two tool functions before the `if __name__` block:

```python
@mcp.tool()
def rebuild_app(name: str) -> str:
    """Trigger a full app rebuild: git pull + docker build + restart. Runs in background, returns a job ID. Poll get_rebuild_status to check progress.

    Args:
        name: App name to rebuild.
    """
    apps = load_apps()
    app = next((a for a in apps if a.name == name), None)
    if app is None:
        return json.dumps({"error": f"App not found: {name}"})

    with _jobs_lock:
        for j in _jobs.values():
            if j.app_name == name and j.status == "running":
                return json.dumps({
                    "error": f"Rebuild already in progress for {name}",
                    "job_id": j.job_id,
                })

    job_id = uuid4().hex[:8]
    job = RebuildJob(
        job_id=job_id,
        app_name=name,
        status="running",
        output_lines=[],
        exit_code=None,
    )
    with _jobs_lock:
        _jobs[job_id] = job

    Thread(target=_run_rebuild, args=(job, app), daemon=True).start()
    return json.dumps({"job_id": job_id, "status": "started"})


@mcp.tool()
def get_rebuild_status(job_id: str) -> str:
    """Check the progress of a running or completed rebuild job.

    Args:
        job_id: Job ID returned by rebuild_app.
    """
    with _jobs_lock:
        job = _jobs.get(job_id)
    if job is None:
        return json.dumps({"error": f"Job not found: {job_id}"})
    with _jobs_lock:
        return json.dumps({
            "job_id": job.job_id,
            "app_name": job.app_name,
            "status": job.status,
            "output_lines": list(job.output_lines),
            "exit_code": job.exit_code,
        }, indent=2)
```

- [ ] **Step 2: Verify tools import**

Run: `cd /home/ifr/sandbox/servertui && .venv/bin/python -c "from mcp_server import rebuild_app, get_rebuild_status; print('rebuild tools OK')"`
Expected: `rebuild tools OK`

- [ ] **Step 3: Commit**

```bash
git add mcp_server.py
git commit -m "Add rebuild_app and get_rebuild_status tools

Async rebuild with background thread and job tracking.
rebuild_app returns job_id, get_rebuild_status polls progress."
```

---

### Task 6: Update README with MCP documentation

**Files:**
- Modify: `README.md`

- [ ] **Step 1: Add MCP Server section to README.md**

Insert the following after the "Refresh Intervals" section (before "## License") in `README.md`:

```markdown
## MCP Server (AI Agent Integration)

ServerTUI includes an MCP server that lets AI agents (like Claude Code) query
status, read logs, manage Docker containers, and trigger app rebuilds.

### Setup

1. Install the MCP dependency:

   ```bash
   source .venv/bin/activate
   pip install mcp
   ```

2. Add to your Claude Code MCP config (`~/.claude/settings.json` or project `.claude/settings.json`):

   ```json
   {
     "mcpServers": {
       "servertui": {
         "command": "/path/to/servertui/.venv/bin/python",
         "args": ["mcp_server.py"],
         "cwd": "/path/to/servertui"
       }
     }
   }
   ```

3. Restart Claude Code. The tools will be available automatically.

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
```

- [ ] **Step 2: Commit**

```bash
git add README.md
git commit -m "Add MCP server documentation to README

Setup instructions, available tools, and example conversation."
```

---

### Task 7: End-to-end verification

**Files:** none (verification only)

- [ ] **Step 1: Verify core.py imports cleanly**

Run: `cd /home/ifr/sandbox/servertui && .venv/bin/python -c "from core import load_apps, App, AppInfo, rebuild_app, docker_container_list, docker_container_stats, docker_action, fetch_app_status, fmt_bytes, run_cmd, detect_build_mode, git_state, inspect_env_file, clone_if_missing, get_clone_status; print('All core exports OK')"`
Expected: `All core exports OK`

- [ ] **Step 2: Verify app.py imports and instantiates without error**

Run: `cd /home/ifr/sandbox/servertui && .venv/bin/python -c "import app; print('TUI module OK')"`
Expected: `TUI module OK`

- [ ] **Step 3: Verify MCP server starts and lists tools**

Run: `cd /home/ifr/sandbox/servertui && echo '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2025-03-26","capabilities":{},"clientInfo":{"name":"test","version":"0.1"}}}' | .venv/bin/python mcp_server.py 2>/dev/null | head -1`
Expected: JSON response with `"serverInfo"` containing `"servertui"`.

- [ ] **Step 4: Verify all 9 MCP tools are registered**

Run: `cd /home/ifr/sandbox/servertui && .venv/bin/python -c "
from mcp_server import mcp
tools = mcp._tool_manager.list_tools()
names = sorted([t.name for t in tools])
print(f'{len(names)} tools: {names}')
assert len(names) == 9, f'Expected 9 tools, got {len(names)}'
print('OK')
"`
Expected: `9 tools: ['docker_restart', 'docker_start', 'docker_stop', 'get_app_logs', 'get_app_status', 'get_container_logs', 'get_docker_containers', 'get_rebuild_status', 'rebuild_app']` followed by `OK`.
