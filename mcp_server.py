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


if __name__ == "__main__":
    mcp.run()
