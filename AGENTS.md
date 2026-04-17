# AGENTS.md

Instructions for AI agents working with this repository.

## MCP Server

ServerTUI provides an MCP server (`servertui mcp`, implemented in `src/servertui/mcp.py`) for managing apps and Docker containers programmatically. If the MCP server is configured, prefer using these tools over shell commands for server operations.

### Available Tools

| Tool | Description |
|------|-------------|
| `get_app_status(name?)` | App state: container status, git state, build mode, env, tunnel. Omit `name` for all apps. |
| `get_docker_containers(brief?)` | All containers. `brief=true` skips CPU/RAM stats (faster). |
| `get_app_logs(name, lines?)` | Docker logs for `servertui-<name>`. Default 100 lines. |
| `get_container_logs(name, lines?)` | Docker logs for any container by name. |
| `docker_start(name)` | Start a stopped container. |
| `docker_stop(name)` | Stop a running container. |
| `docker_restart(name)` | Restart a container. |
| `rebuild_app(name)` | Trigger git pull + docker build + restart. Returns a `job_id`. |
| `get_rebuild_status(job_id)` | Poll rebuild progress. Returns status, output lines, exit code. |

### Workflows

**Check if something is running:**
1. Call `get_app_status(name="myapp")` or `get_docker_containers(brief=true)`
2. Report the `container_status` and `git_state` fields

**Redeploy an app:**
1. Call `rebuild_app(name="myapp")` — returns `{"job_id": "abc123", "status": "started"}`
2. Poll `get_rebuild_status(job_id="abc123")` until `status` is `"done"` or `"failed"`
3. Report the result and exit code to the user
4. If failed, check `output_lines` for the error and optionally `get_app_logs` for runtime logs

**Diagnose a problem:**
1. `get_app_status` to check container/git/tunnel state
2. `get_app_logs` or `get_container_logs` to read recent logs
3. If the container is stopped, consider `docker_start` or `rebuild_app`

### Tips

- Use `brief=true` on `get_docker_containers` when you only need status, not CPU/RAM stats (avoids ~1-2s per container delay)
- `rebuild_app` is async — always poll `get_rebuild_status` afterward, don't assume success
- App containers are named `servertui-<name>` — use the app name with `get_app_logs`, the full container name with `get_container_logs`
- Only one rebuild per app can run at a time; a second call returns an error with the existing job ID
