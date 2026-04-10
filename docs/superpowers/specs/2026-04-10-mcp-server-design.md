# ServerTUI MCP Server — Design Spec

## Goal

Expose ServerTUI's app and Docker operations as an MCP (Model Context Protocol) server so Claude Code can query state, read logs, manage containers, and trigger rebuilds conversationally.

## Scope

Phase 1 focuses on **Apps** and **Docker containers** only. Tunnels, Ollama, system stats, and timers are out of scope and can be added later.

## File Structure

```
servertui/
  core.py           ← NEW: shared logic extracted from app.py
  mcp_server.py     ← NEW: MCP server exposing tools
  app.py            ← MODIFIED: imports from core.py instead of defining inline
  run.sh            ← unchanged
  README.md         ← MODIFIED: new "MCP Server" section
```

## core.py — Shared Logic

Extract the following from `app.py` into `core.py` with no TUI dependencies:

### Config & data types
- `CONFIG_DIR`, `APPS_CONFIG`, `ENV_DIR`, `APPS_DIR` path constants
- `App` dataclass (name, git_url, tunnel, branch, compose_file, repo_path property)
- `AppInfo` dataclass (name, container_status, image, uptime, tunnel, tunnel_status, git_state, env_key_count, env_perms_ok, build_mode)
- `load_apps() -> list[App]`

### Clone management
- `clone_if_missing(app)` — clone repo if not present, updates `_clone_status`
- `get_clone_status(app_name) -> str`
- `_clone_status` dict and `_clone_lock`

### Helpers
- `run_cmd(cmd, timeout) -> str`
- `fmt_bytes(n) -> str`
- `detect_build_mode(repo_path, compose_file) -> str`
- `git_state(repo_path) -> str`
- `inspect_env_file(path) -> tuple[int | None, bool]`

### Docker operations
- `docker_container_list() -> list[dict]` — returns container name, status, image (cheap, no stats)
- `docker_container_stats() -> list[dict]` — returns containers with CPU%, mem usage/limit (expensive)
- `docker_action(name, action) -> str` — start/stop/restart a container by name, returns success/error message

### App operations
- `fetch_app_status(apps, tunnel_data) -> list[AppInfo]` — assemble app state (extracted from `DataStore.fetch_apps`)
- `rebuild_app(app) -> Generator[str, None, None]` — the existing generator that yields output lines, unchanged

### What stays in app.py
- `DataStore` class and `STORE` global
- `bg_fetch_expensive()`, `bg_fetch_cheap()`
- `REBUILD_LOCK` (TUI-specific; MCP server has its own)
- All panel widgets (`SystemPanel`, `TunnelPanel`, `DockerPanel`, `OllamaPanel`, `AppPanel`, `TimerPanel`)
- All modal screens (`SelectorScreen`, `LogScreen`, `BuildScreen`)
- `ServerTUI` app class, keybindings, action methods
- `edit_env_file()` (interactive, requires terminal)
- `systemctl_user()`, `detect_tunnel_domain()`, `get_uptime()`, `bar()`, `ollama_api()` — tunnel/system/ollama helpers stay in app.py until those features are added to MCP

## MCP Server Tools

### Read tools

#### `get_docker_containers`
- **Description:** List all Docker containers with status, image, CPU%, and RAM usage.
- **Parameters:**
  - `brief` (optional, default false) — if true, skip expensive CPU/RAM stats and return only name, status, image
- **Returns:** JSON array of objects:
  ```json
  [
    {
      "name": "coloring-cafe",
      "status": "running",
      "image": "coloring-cafe:latest",
      "cpu_pct": 2.3,
      "mem_usage": 134217728,
      "mem_limit": 8589934592
    }
  ]
  ```
  When `brief=true`, `cpu_pct`, `mem_usage`, and `mem_limit` are omitted.
- **Notes:** Full stats take ~1-2s per container. Use `brief=true` when only status is needed.

#### `get_app_status`
- **Description:** List all configured apps with container status, git state, build mode, env info, and tunnel status.
- **Parameters:** `name` (optional) — filter to a single app
- **Returns:** JSON array of `AppInfo` objects:
  ```json
  [
    {
      "name": "myapp",
      "container_status": "running",
      "image": "servertui-myapp:latest",
      "uptime": "2d",
      "tunnel": "myapp",
      "tunnel_status": "active",
      "git_state": "clean",
      "env_key_count": 5,
      "env_perms_ok": true,
      "build_mode": "compose"
    }
  ]
  ```

#### `get_app_logs`
- **Description:** Get recent Docker logs for an app's container.
- **Parameters:**
  - `name` (required) — app name (container will be `servertui-<name>`)
  - `lines` (optional, default 100) — number of tail lines
- **Returns:** Plain text log output.

#### `get_container_logs`
- **Description:** Get recent Docker logs for any container by name.
- **Parameters:**
  - `name` (required) — container name
  - `lines` (optional, default 100) — number of tail lines
- **Returns:** Plain text log output.

#### `get_rebuild_status`
- **Description:** Check the progress of a running or completed rebuild job.
- **Parameters:**
  - `job_id` (required) — job ID returned by `rebuild_app`
- **Returns:** JSON object:
  ```json
  {
    "job_id": "abc123",
    "app_name": "myapp",
    "status": "running",
    "output_lines": ["$ git pull --ff-only", "Already up to date.", "$ docker build ..."],
    "exit_code": null
  }
  ```
  `status` is `"running"`, `"done"`, or `"failed"`. `exit_code` is null while running.

### Write tools

#### `docker_start`
- **Description:** Start a stopped Docker container.
- **Parameters:** `name` (required) — container name
- **Returns:** Success or error message.

#### `docker_stop`
- **Description:** Stop a running Docker container.
- **Parameters:** `name` (required) — container name
- **Returns:** Success or error message.

#### `docker_restart`
- **Description:** Restart a Docker container.
- **Parameters:** `name` (required) — container name
- **Returns:** Success or error message.

#### `rebuild_app`
- **Description:** Trigger a full app rebuild: git pull + docker build + restart. Runs in background.
- **Parameters:** `name` (required) — app name
- **Returns:** JSON: `{"job_id": "abc123", "status": "started"}`
- **Notes:** Only one rebuild per app at a time. Returns error if a rebuild for the same app is already running.

## Rebuild Job Tracking

`mcp_server.py` maintains an in-memory dict:

```python
@dataclass
class RebuildJob:
    job_id: str
    app_name: str
    status: str             # "running" | "done" | "failed"
    output_lines: list[str]
    exit_code: int | None

_jobs: dict[str, RebuildJob] = {}
_jobs_lock: Lock
```

When `rebuild_app` is called:
1. Generate a short job ID (e.g., `uuid4().hex[:8]`)
2. Check no other job for the same app is currently `"running"`
3. Create a `RebuildJob` with status `"running"`
4. Spawn a daemon thread that consumes `core.rebuild_app(app)`, appending each yielded line to `output_lines`
5. When the generator finishes, parse the final `[exit N]` line to set `exit_code` and `status` (`"done"` if 0, `"failed"` otherwise)
6. Return `{job_id, status: "started"}` immediately

Jobs are kept in memory indefinitely for the server's lifetime (they're small). No persistence needed.

## MCP Server Entry Point

Uses the `mcp` Python SDK (`pip install mcp`).

```python
# mcp_server.py
from mcp.server.fastmcp import FastMCP

mcp = FastMCP("servertui")

@mcp.tool()
def get_docker_containers(brief: bool = False) -> list[dict]: ...

@mcp.tool()
def get_app_status(name: str | None = None) -> list[dict]: ...

# ... etc

if __name__ == "__main__":
    mcp.run()
```

Runs via stdio transport (default for Claude Code).

## README Addition

Add a new section to README.md after "Keybindings":

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

## Dependencies

New: `mcp` (pip install). No other new dependencies.

## What Does NOT Change

- TUI behavior is identical — `app.py` just imports from `core.py`
- `apps.json` config format unchanged
- `run.sh` unchanged
- No new config files required
- Existing keybindings unchanged
