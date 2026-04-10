"""
ServerTUI MCP Server — exposes app and Docker operations as MCP tools
for AI agent integration (e.g., Claude Code).
"""

import json
import re
import subprocess
from dataclasses import dataclass
from threading import Lock, Thread
from uuid import uuid4

from mcp.server.fastmcp import FastMCP

from core import (
    App,
    docker_action,
    docker_container_list,
    docker_container_stats,
    fetch_app_status,
    load_apps,
    rebuild_app as core_rebuild_app,
)

mcp = FastMCP("servertui")


@dataclass
class RebuildJob:
    job_id: str
    app_name: str
    status: str             # "running" | "done" | "failed"
    output_lines: list[str]
    exit_code: int | None


_jobs: dict[str, RebuildJob] = {}
_jobs_lock = Lock()


def _strip_rich_markup(text: str) -> str:
    """Remove Rich markup tags like [red], [/red], [bold], [dim], etc."""
    return re.sub(r"\[/?[a-z_ ]+\]", "", text)


def _run_rebuild(job: RebuildJob, app: App) -> None:
    """Background thread: consume rebuild_app generator and update job state."""
    try:
        for line in core_rebuild_app(app):
            clean = _strip_rich_markup(line)
            with _jobs_lock:
                job.output_lines.append(clean)
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
    if containers is None:
        return json.dumps({"error": "Docker daemon not reachable"})
    if not containers:
        return json.dumps({"info": "No containers found"})
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
        return json.dumps({
            "job_id": job.job_id,
            "app_name": job.app_name,
            "status": job.status,
            "output_lines": list(job.output_lines),
            "exit_code": job.exit_code,
        }, indent=2)


if __name__ == "__main__":
    mcp.run()
