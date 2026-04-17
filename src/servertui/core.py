"""
core.py — Shared logic for ServerTUI.

Extracted from tui.py so both the TUI and MCP server can import
config, data types, helpers, docker operations, app status, and
rebuild logic.
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
    name: str               # display name; container will be servertui-<name>
    git_url: str            # git remote URL (SSH or HTTPS)
    tunnel: str | None = None  # bare tunnel name, e.g. "foo" (NOT "cloudflared-foo.service")
    branch: str | None = None  # branch to clone/checkout; None = repo default
    compose_file: str | None = None  # override compose filename, relative to repo root

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

# Clone status tracking: app name -> "cloning" | "done" | error message
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


# ─── Data types ──────────────────────────────────────────────────────

@dataclass
class AppInfo:
    name: str
    container_status: str          # "running" | "stopped" | "missing"
    image: str | None
    uptime: str | None
    tunnel: str | None
    tunnel_status: str | None      # "active" | "inactive" | None
    git_state: str                 # "clean" | "dirty" | f"behind {n}" | "?" | "cloning" | "clone-failed"
    env_key_count: int | None      # None if file missing
    env_perms_ok: bool             # True if file missing OR mode == 0o600
    build_mode: str                # "dockerfile" | "compose" | "none"

    def to_dict(self) -> dict:
        return asdict(self)


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


def parse_env_file(path: Path) -> list[tuple[str, str]]:
    """Parse a .env-style file into an ordered list of (key, value) pairs.

    Handles KEY=value, `export KEY=value`, quoted values ("..." or '...'),
    inline # comments in unquoted values, and blank/comment lines.
    Malformed lines are logged to stderr and skipped; parsing never raises.
    Duplicate keys: last wins (matches Docker --env-file behavior).
    """
    key_re = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
    pairs: dict[str, str] = {}
    order: list[str] = []
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError as e:
        print(f"[servertui] cannot read {path}: {e}", file=sys.stderr)
        return []

    for lineno, raw in enumerate(text.splitlines(), 1):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[len("export "):].lstrip()
        if "=" not in line:
            print(f"[servertui] {path}:{lineno}: no '=' in line, skipping",
                  file=sys.stderr)
            continue
        key, _, rest = line.partition("=")
        key = key.strip()
        if not key_re.match(key):
            print(f"[servertui] {path}:{lineno}: invalid key {key!r}, skipping",
                  file=sys.stderr)
            continue
        value = rest.lstrip()
        if value.startswith('"') or value.startswith("'"):
            quote = value[0]
            end = -1
            i = 1
            while i < len(value):
                ch = value[i]
                if quote == '"' and ch == "\\" and i + 1 < len(value):
                    i += 2
                    continue
                if ch == quote:
                    end = i
                    break
                i += 1
            if end == -1:
                print(f"[servertui] {path}:{lineno}: unterminated quote, skipping",
                      file=sys.stderr)
                continue
            inner = value[1:end]
            if quote == '"':
                out = []
                i2 = 0
                esc_map = {"n": "\n", "r": "\r", "t": "\t",
                           '"': '"', "\\": "\\"}
                while i2 < len(inner):
                    if inner[i2] == "\\" and i2 + 1 < len(inner):
                        nxt = inner[i2 + 1]
                        out.append(esc_map.get(nxt, "\\" + nxt))
                        i2 += 2
                    else:
                        out.append(inner[i2])
                        i2 += 1
                inner = "".join(out)
            value = inner
        else:
            hash_idx = -1
            for i, ch in enumerate(value):
                if ch == "#" and (i == 0 or value[i - 1].isspace()):
                    hash_idx = i
                    break
            if hash_idx != -1:
                value = value[:hash_idx]
            value = value.rstrip()
        if key in pairs:
            pairs[key] = value
        else:
            pairs[key] = value
            order.append(key)
    return [(k, pairs[k]) for k in order]


def _quote_env_value(value: str) -> str:
    """Emit a .env-compatible serialization of value."""
    if value == "":
        return ""
    safe = set("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ"
               "0123456789_./:@+-")
    if all(ch in safe for ch in value):
        return value
    escaped = (value.replace("\\", "\\\\")
                    .replace('"', '\\"')
                    .replace("\n", "\\n")
                    .replace("\r", "\\r")
                    .replace("\t", "\\t"))
    return f'"{escaped}"'


def merge_env_keys(
    canonical_path: Path,
    updates: list[tuple[str, str]],
    source_label: str,
) -> int:
    """Write updates into canonical_path, preserving comments and unrelated keys.

    - Creates the file atomically (0600, O_EXCL) if missing, seeded with a
      minimal header.
    - Replaces existing KEY=... lines in place for keys present in canonical.
    - Appends new keys under a dated `# imported …` marker.
    - If a key appears multiple times in canonical, replaces the last
      occurrence and deletes earlier ones (opportunistic dedup).
    - Atomic write via <path>.tmp + os.replace.

    Returns len(updates) on success. Raises OSError on filesystem errors.
    """
    if not updates:
        return 0

    if not canonical_path.exists():
        header = (
            f"# ServerTUI env file for '{canonical_path.stem}'\n"
            f"# Location: {canonical_path} (0600, injected via docker --env-file).\n"
            f"# Stored outside the git repo so secrets stay uncommitted.\n"
        ).encode()
        try:
            fd = os.open(canonical_path,
                         os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            try:
                os.write(fd, header)
            finally:
                os.close(fd)
        except FileExistsError:
            pass

    text = canonical_path.read_text(encoding="utf-8", errors="replace")
    lines = text.splitlines(keepends=True)

    key_re = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
    key_lines: dict[str, list[int]] = {}
    for idx, raw in enumerate(lines):
        stripped = raw.strip()
        if not stripped or stripped.startswith("#"):
            continue
        body = stripped
        if body.startswith("export "):
            body = body[len("export "):].lstrip()
        if "=" not in body:
            continue
        key = body.split("=", 1)[0].strip()
        if key_re.match(key):
            key_lines.setdefault(key, []).append(idx)

    in_place: list[tuple[str, str]] = []
    appended: list[tuple[str, str]] = []
    for key, value in updates:
        if key in key_lines:
            in_place.append((key, value))
        else:
            appended.append((key, value))

    to_delete: set[int] = set()
    for key, value in in_place:
        indices = key_lines[key]
        last = indices[-1]
        lines[last] = f"{key}={_quote_env_value(value)}\n"
        for earlier in indices[:-1]:
            to_delete.add(earlier)
    if to_delete:
        lines = [line for i, line in enumerate(lines) if i not in to_delete]

    if appended:
        if lines and not lines[-1].endswith("\n"):
            lines[-1] = lines[-1] + "\n"
        today = datetime.now().date().isoformat()
        lines.append("\n")
        lines.append(f"# imported {today} from {source_label}\n")
        for key, value in appended:
            lines.append(f"{key}={_quote_env_value(value)}\n")

    tmp_path = canonical_path.with_suffix(canonical_path.suffix + ".tmp")
    if tmp_path.exists():
        tmp_path.unlink()
    fd = os.open(tmp_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        os.write(fd, "".join(lines).encode("utf-8"))
    finally:
        os.close(fd)
    os.replace(tmp_path, canonical_path)
    return len(updates)


def detect_build_mode(repo_path: Path, compose_file: str | None = None) -> str:
    """Return 'compose', 'dockerfile', or 'none'.
    If compose_file is set, only that file counts as compose mode."""
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


# ─── Docker operations ───────────────────────────────────────────────

def docker_container_list() -> list[dict] | None:
    """Cheap: return name, status, image for all containers (no stats).
    Returns None when Docker is unreachable."""
    try:
        client = docker.from_env()
        client.ping()
    except Exception:
        return None

    containers = []
    for c in client.containers.list(all=True):
        containers.append({
            "name": c.name,
            "status": c.status,
            "image": c.image.tags[0] if c.image.tags else c.short_id,
        })
    containers.sort(key=lambda c: (c["status"] != "running", c["name"]))
    return containers


def docker_container_stats() -> list[dict] | None:
    """Expensive: return name, status, image, cpu_pct, mem_usage, mem_limit for all containers.
    Returns None when Docker is unreachable."""
    try:
        client = docker.from_env()
        client.ping()
    except Exception:
        return None

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
    """Perform a Docker action (start/stop/restart) on a container by name.
    Returns a status message string."""
    try:
        client = docker.from_env()
        container = client.containers.get(name)
        getattr(container, action)()
        return f"{action.capitalize()}ed {name}"
    except docker.errors.NotFound:
        return f"Container not found: {name}"
    except Exception as e:
        return f"Error: {e}"


# ─── App status ──────────────────────────────────────────────────────

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
        # Verify the container is actually gone before trying to create it
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
