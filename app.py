"""
ServerTUI — Local server dashboard for managing Cloudflare tunnels,
Docker containers, apps (local repos), and monitoring system resources.
"""

import os
import re
import socket
import subprocess
import shlex
import json
import urllib.request
import urllib.error
from datetime import datetime
from threading import Thread, Lock
from dataclasses import dataclass
from pathlib import Path

import docker
import psutil
from textual.app import App, ComposeResult
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

# ─── Config ──────────────────────────────────────────────────────────

## Tunnels are auto-discovered from `cloudflared-*.service` user units.
## Add entries here only to override description/domain or pin ordering.
TUNNELS: list[dict] = []

## Apps are locally-cloned repos that ServerTUI can manually rebuild and whose
## env files it manages. Each app owns exactly one container named
## `servertui-<name>`. ServerTUI will never touch containers it didn't name.
@dataclass(frozen=True)
class App:
    name: str               # display name; container will be servertui-<name>
    repo_path: Path         # absolute path to a local git clone
    tunnel: str | None = None  # optional cross-reference to a TUNNELS service name

APPS: list[App] = []

ENV_DIR = Path.home() / ".config" / "servertui" / "env"


@dataclass
class AppInfo:
    name: str
    container_status: str          # "running" | "stopped" | "missing"
    image: str | None
    uptime: str | None
    tunnel: str | None
    tunnel_status: str | None      # "active" | "inactive" | None
    git_state: str                 # "clean" | "dirty" | f"behind {n}" | "?"
    env_key_count: int | None      # None if file missing
    env_perms_ok: bool             # True if file missing OR mode == 0o600
    build_mode: str                # "dockerfile" | "compose" | "none"

OLLAMA_BASE = "http://localhost:11434"


# ─── Helpers ─────────────────────────────────────────────────────────

def run_cmd(cmd: str, timeout: int = 5) -> str:
    try:
        result = subprocess.run(
            shlex.split(cmd), capture_output=True, text=True, timeout=timeout,
        )
        return result.stdout.strip()
    except Exception as e:
        return f"error: {e}"


def systemctl_user(action: str, service: str) -> str:
    return run_cmd(f"systemctl --user {action} {service}")


def detect_tunnel_domain(service: str) -> str:
    """Best-effort hostname extraction from a cloudflared unit's config file."""
    raw = run_cmd(f"systemctl --user show {service} --property=ExecStart --no-pager")
    m = re.search(r"--config[= ]([^\s;]+)", raw)
    candidates = []
    if m:
        candidates.append(m.group(1))
    home = os.path.expanduser("~")
    candidates += [
        f"{home}/.cloudflared/{service.removeprefix('cloudflared-')}.yml",
        f"{home}/.cloudflared/config.yml",
        f"/etc/cloudflared/{service.removeprefix('cloudflared-')}.yml",
        "/etc/cloudflared/config.yml",
    ]
    seen = set()
    for path in candidates:
        if not path or path in seen or not os.path.isfile(path):
            seen.add(path)
            continue
        seen.add(path)
        try:
            with open(path) as f:
                for line in f:
                    hm = re.search(r"hostname:\s*([^\s#]+)", line)
                    if hm:
                        return hm.group(1).strip("\"'")
        except OSError:
            continue
    return ""


def fmt_bytes(n: int | float) -> str:
    if n < 0:
        return "N/A"
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if abs(n) < 1024:
            return f"{n:.1f} {unit}"
        n /= 1024
    return f"{n:.1f} PB"


def bar(percent: float, width: int = 20) -> str:
    filled = int(width * percent / 100)
    empty = width - filled
    if percent > 90:
        color = "red"
    elif percent > 70:
        color = "yellow"
    else:
        color = "green"
    return f"[{color}]{'█' * filled}{'░' * empty}[/]"


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


def detect_build_mode(repo_path: Path) -> str:
    """Return 'compose', 'dockerfile', or 'none'."""
    if not repo_path.is_dir():
        return "none"
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


def get_uptime() -> str:
    boot = datetime.fromtimestamp(psutil.boot_time())
    delta = datetime.now() - boot
    days = delta.days
    hours, rem = divmod(delta.seconds, 3600)
    mins, _ = divmod(rem, 60)
    parts = []
    if days:
        parts.append(f"{days}d")
    if hours:
        parts.append(f"{hours}h")
    parts.append(f"{mins}m")
    return " ".join(parts)


def ollama_api(endpoint: str) -> dict | None:
    try:
        req = urllib.request.Request(f"{OLLAMA_BASE}{endpoint}")
        with urllib.request.urlopen(req, timeout=3) as resp:
            return json.loads(resp.read())
    except Exception:
        return None


# ─── Background data store ───────────────────────────────────────────
# All expensive I/O runs in a thread. The UI only reads from this cache.

class DataStore:
    """Thread-safe cache for all dashboard data."""

    def __init__(self):
        self._lock = Lock()
        self._data = {
            "system": {},
            "tunnels": [],
            "timers": [],
            "docker": [],
            "ollama": {},
            "apps": [],
        }

    def get(self, key: str):
        with self._lock:
            return self._data.get(key)

    def set(self, key: str, value):
        with self._lock:
            self._data[key] = value

    def fetch_system(self):
        """Fetch system stats (cheap, ~instant)."""
        cpu_percent = psutil.cpu_percent(interval=None)
        cpu_count = psutil.cpu_count()
        cpu_freq = psutil.cpu_freq()
        mem = psutil.virtual_memory()
        swap = psutil.swap_memory()
        disk = psutil.disk_usage("/")
        net = psutil.net_io_counters()
        load1, load5, load15 = psutil.getloadavg()

        self.set("system", {
            "cpu_percent": cpu_percent,
            "cpu_count": cpu_count,
            "cpu_freq": f"{cpu_freq.current:.0f} MHz" if cpu_freq else "N/A",
            "mem": mem,
            "swap": swap,
            "disk": disk,
            "net_sent": net.bytes_sent,
            "net_recv": net.bytes_recv,
            "load": (load1, load5, load15),
            "uptime": get_uptime(),
        })

    def fetch_tunnels(self):
        """Fetch tunnel statuses (cheap, ~instant).

        Auto-discovers any cloudflared-*.service user unit. Entries in the
        TUNNELS constant act as optional metadata overlays (description/domain)
        keyed by service name.
        """
        meta = {t["service"]: t for t in TUNNELS}

        raw_units = run_cmd(
            "systemctl --user list-unit-files 'cloudflared-*.service' "
            "--no-legend --no-pager"
        )
        services = []
        for line in raw_units.splitlines():
            parts = line.split()
            if parts and parts[0].endswith(".service"):
                services.append(parts[0][: -len(".service")])

        # Preserve TUNNELS ordering first, then any extras discovered.
        ordered = [t["service"] for t in TUNNELS if t["service"] in services]
        ordered += [s for s in services if s not in ordered]

        tunnels = []
        for service in ordered:
            raw = run_cmd(
                f"systemctl --user show {service} "
                "--property=ActiveState,SubState,MainPID,MemoryCurrent "
                "--no-pager"
            )
            info = {}
            for line in raw.splitlines():
                if "=" in line:
                    k, v = line.split("=", 1)
                    info[k] = v
            base = meta.get(service) or {
                "name": service.removeprefix("cloudflared-"),
                "service": service,
                "domain": detect_tunnel_domain(service),
                "description": service.removeprefix("cloudflared-").title(),
            }
            tunnels.append({**base, **info})
        self.set("tunnels", tunnels)

    def fetch_timers(self):
        """Fetch systemd --user timers (cheap)."""
        raw = run_cmd(
            "systemctl --user list-timers --all --no-legend --no-pager"
        )
        timers = []
        seen = set()
        for line in raw.splitlines():
            parts = line.split()
            # Find the *.timer token; everything before it is time data,
            # everything after is the activated unit(s).
            unit_idx = next(
                (i for i, p in enumerate(parts) if p.endswith(".timer")),
                None,
            )
            if unit_idx is None:
                continue
            unit = parts[unit_idx]
            if unit in seen:
                continue
            seen.add(unit)
            activates = " ".join(parts[unit_idx + 1:]) or ""
            time_parts = parts[:unit_idx]
            # Split time_parts in half: first half = NEXT+LEFT, second = LAST+PASSED
            mid = len(time_parts) // 2
            entry = {
                "next_left": " ".join(time_parts[:mid]),
                "last_passed": " ".join(time_parts[mid:]),
                "next": " ".join(time_parts[:mid]),
                "left": "",
                "last": " ".join(time_parts[mid:]),
                "passed": "",
                "unit": unit,
                "activates": activates,
            }
            show = run_cmd(
                f"systemctl --user show {entry['unit']} "
                "--property=ActiveState,SubState,Description --no-pager"
            )
            for kv in show.splitlines():
                if "=" in kv:
                    k, v = kv.split("=", 1)
                    entry[k] = v
            timers.append(entry)
        self.set("timers", timers)

    def fetch_docker(self):
        """Fetch docker container list + stats (EXPENSIVE — runs in bg)."""
        try:
            client = docker.from_env()
            client.ping()
        except Exception:
            self.set("docker", None)
            return

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
        self.set("docker", containers)

    def fetch_ollama(self):
        """Fetch ollama status (cheap, HTTP calls)."""
        version = ollama_api("/api/version")
        tags = ollama_api("/api/tags")
        ps = ollama_api("/api/ps")
        self.set("ollama", {
            "online": version is not None,
            "version": version.get("version", "?") if version else "?",
            "models": tags.get("models", []) if tags else [],
            "running": ps.get("models", []) if ps else [],
        })

    def fetch_apps(self):
        """Snapshot state of every configured app. Cheap: stat + 2 git + 1 docker inspect per app."""
        tunnels = self._data.get("tunnels") or []
        tunnel_status_by_service = {}
        for t in tunnels:
            state = t.get("ActiveState", "unknown")
            sub = t.get("SubState", "unknown")
            tunnel_status_by_service[t.get("service", "")] = (
                "active" if (state == "active" and sub == "running") else "inactive"
            )

        try:
            client = docker.from_env()
            docker_ok = True
        except Exception:
            client = None
            docker_ok = False

        out: list[AppInfo] = []
        for app in APPS:
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
                f"cloudflared-{app.tunnel}.service" if app.tunnel else None
            )
            tunnel_status = (
                tunnel_status_by_service.get(tunnel_service) if tunnel_service else None
            )

            out.append(AppInfo(
                name=app.name,
                container_status=status,
                image=image,
                uptime=uptime,
                tunnel=app.tunnel,
                tunnel_status=tunnel_status,
                git_state=git_state(app.repo_path),
                env_key_count=env_count,
                env_perms_ok=env_perms_ok,
                build_mode=detect_build_mode(app.repo_path),
            ))

        self.set("apps", out)


STORE = DataStore()
REBUILD_LOCK = Lock()


def bg_fetch_expensive():
    """Background thread: fetch Docker stats (slow) + ollama."""
    STORE.fetch_docker()
    STORE.fetch_ollama()


def bg_fetch_cheap():
    """Fetch quick data (system + tunnels) — can run on main timer."""
    STORE.fetch_system()
    STORE.fetch_tunnels()
    STORE.fetch_timers()
    STORE.fetch_apps()


def rebuild_app(app: "App"):
    """Generator that yields output lines from git pull + build + restart.
    Final yielded value is a string '[exit 0]' or '[exit N]'."""
    container = f"servertui-{app.name}"
    env_path = ENV_DIR / f"{app.name}.env"

    if not app.repo_path.is_dir():
        yield f"[red]repo path does not exist: {app.repo_path}[/]"
        yield "[exit 1]"
        return

    mode = detect_build_mode(app.repo_path)
    if mode == "none":
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
        yield "[red]git pull failed — aborting[/]"
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
            yield "[red]docker build failed — existing container untouched[/]"
            yield f"[exit {rc}]"
            return

        yield "[dim]$ docker rm -f {}[/]".format(container)
        subprocess.run(["docker", "rm", "-f", container],
                       capture_output=True, text=True)

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
    compose_file = app.repo_path / "compose.yml"
    if not compose_file.exists():
        compose_file = app.repo_path / "docker-compose.yml"
    cmd = ["docker", "compose", "-f", str(compose_file)]
    if env_path.exists():
        cmd += ["--env-file", str(env_path)]
    cmd += ["up", "-d", "--build"]
    rc = None
    for item in stream(cmd, cwd=app.repo_path):
        if isinstance(item, int):
            rc = item
        else:
            yield item
    yield f"[exit {rc}]"


def edit_env_file(app_cfg: "App") -> tuple[bool, str | None]:
    """Open the app's env file in $EDITOR. Creates it 0600 if missing.
    Returns (changed, error). `changed` is True iff mtime advanced."""
    try:
        ENV_DIR.mkdir(parents=True, exist_ok=True)
        os.chmod(ENV_DIR, 0o700)
    except OSError as e:
        return (False, f"cannot create {ENV_DIR}: {e}")

    path = ENV_DIR / f"{app_cfg.name}.env"
    if not path.exists():
        try:
            path.touch(mode=0o600)
        except OSError as e:
            return (False, f"cannot create {path}: {e}")
    else:
        st = path.stat()
        if (st.st_mode & 0o777) != 0o600:
            return (False, f"refusing to edit: {path} has perms "
                           f"{oct(st.st_mode & 0o777)}, expected 0o600")

    editor = os.environ.get("EDITOR") or "nano"
    before = path.stat().st_mtime

    try:
        proc = subprocess.run([editor, str(path)])
    except FileNotFoundError:
        return (False, f"editor not found: {editor} (set $EDITOR)")

    if proc.returncode != 0:
        return (False, f"{editor} exited with code {proc.returncode}")

    after = path.stat().st_mtime
    return (after > before, None)


# ─── Widgets ─────────────────────────────────────────────────────────

class SystemPanel(Static):
    def compose(self) -> ComposeResult:
        yield Static(id="sys-content")

    def refresh_data(self) -> None:
        s = STORE.get("system")
        if not s:
            self.query_one("#sys-content", Static).update("[dim]Loading...[/]")
            return

        mem = s["mem"]
        swap = s["swap"]
        disk = s["disk"]

        self.query_one("#sys-content", Static).update(
            f"[bold cyan]═══ 🖥️  System Resources ═══[/]\n\n"
            f"  ⏱️  [bold]Uptime:[/]  {s['uptime']}\n"
            f"  📊 [bold]Load:[/]    {s['load'][0]:.2f}  {s['load'][1]:.2f}  {s['load'][2]:.2f}\n\n"
            f"  🧠 [bold cyan]CPU[/]     {bar(s['cpu_percent'])}  {s['cpu_percent']:5.1f}%\n"
            f"            {s['cpu_count']} cores @ {s['cpu_freq']}\n\n"
            f"  💾 [bold green]RAM[/]     {bar(mem.percent)}  {mem.percent:5.1f}%\n"
            f"            {fmt_bytes(mem.used)} / {fmt_bytes(mem.total)}\n\n"
            f"  🔁 [bold yellow]Swap[/]    {bar(swap.percent)}  {swap.percent:5.1f}%\n"
            f"            {fmt_bytes(swap.used)} / {fmt_bytes(swap.total)}\n\n"
            f"  💽 [bold magenta]Disk /[/]  {bar(disk.percent)}  {disk.percent:5.1f}%\n"
            f"            {fmt_bytes(disk.used)} / {fmt_bytes(disk.total)}\n\n"
            f"  ⬆️  [bold blue]Net ↑[/]   {fmt_bytes(s['net_sent'])}\n"
            f"  ⬇️  [bold blue]Net ↓[/]   {fmt_bytes(s['net_recv'])}\n"
        )


class TunnelPanel(Static):
    def compose(self) -> ComposeResult:
        yield Static(id="tunnel-content")

    def refresh_data(self) -> None:
        tunnels = STORE.get("tunnels")
        if not tunnels:
            self.query_one("#tunnel-content", Static).update("[dim]Loading...[/]")
            return

        lines = ["[bold cyan]═══ ☁️  Cloudflare Tunnels ═══[/]\n"]
        for t in tunnels:
            state = t.get("ActiveState", "unknown")
            sub = t.get("SubState", "unknown")
            pid = t.get("MainPID", "0")
            mem_raw = t.get("MemoryCurrent", "[not set]")

            if state == "active" and sub == "running":
                icon = "🟢"
                status_str = "[green]running[/]"
            elif state == "active":
                icon = "🟡"
                status_str = f"[yellow]{sub}[/]"
            else:
                icon = "🔴"
                status_str = f"[red]{state}[/]"

            try:
                mem_str = fmt_bytes(int(mem_raw))
            except (ValueError, TypeError):
                mem_str = "N/A"

            domain = t.get("domain") or "[dim]—[/]"
            lines.append(
                f"  {icon}  [bold]{t['description']:<24}[/] {status_str}\n"
                f"       🌐 {domain:<26} 🆔 {pid:<6} 💾 {mem_str}\n"
            )

        lines.append("[dim]  ⌨  [bold]s[/]=start  [bold]t[/]=stop  [bold]r[/]=restart  [bold]l[/]=logs[/]")
        self.query_one("#tunnel-content", Static).update("\n".join(lines))


class DockerPanel(Static):
    def compose(self) -> ComposeResult:
        yield Static(id="docker-content")

    def refresh_data(self) -> None:
        containers = STORE.get("docker")
        lines = ["[bold cyan]═══ 🐳 Docker Containers ═══[/]\n"]

        if containers is None:
            lines.append("  ⚠️  [red]Docker daemon not reachable[/]")
        elif not containers:
            lines.append("  [dim]📭 No containers found[/]")
        else:
            for c in containers:
                name = c["name"]
                status = c["status"]
                image = c["image"]

                if status == "running":
                    icon = "🟢"
                    status_str = f"[green]{status}[/]"
                    mem_str = (
                        f"{fmt_bytes(c['mem_usage'])} / {fmt_bytes(c['mem_limit'])}"
                        if c["mem_usage"] else "N/A"
                    )
                    lines.append(
                        f"  {icon}  [bold]{name:<28}[/] {status_str}\n"
                        f"       📦 {image}\n"
                        f"       🧠 CPU {c['cpu_pct']:.1f}%   💾 RAM {mem_str}\n"
                    )
                else:
                    icon = "🔴"
                    lines.append(
                        f"  {icon}  [bold]{name:<28}[/] [red]{status}[/]\n"
                        f"       📦 {image}\n"
                    )

        lines.append("[dim]  ⌨  [bold]u[/]=start  [bold]d[/]=stop  [bold]x[/]=restart[/]")
        self.query_one("#docker-content", Static).update("\n".join(lines))


class OllamaPanel(Static):
    def compose(self) -> ComposeResult:
        yield Static(id="ollama-content")

    def refresh_data(self) -> None:
        status = STORE.get("ollama") or {}
        lines = ["[bold cyan]═══ 🦙 Ollama LLM ═══[/]\n"]

        if not status.get("online"):
            lines.append("  🔴 [red]Offline[/]  [dim]Ollama not running[/]\n")
            self.query_one("#ollama-content", Static).update("\n".join(lines))
            return

        lines.append(f"  🟢 [green]Online[/]  v{status['version']}\n")

        running = status.get("running", [])
        if running:
            lines.append("  ⚡ [bold green]Loaded in memory:[/]")
            for m in running:
                name = m.get("name", "?")
                size = m.get("size", 0)
                vram = m.get("size_vram", 0)
                ram = size - vram
                details = m.get("details", {})
                params = details.get("parameter_size", "")
                quant = details.get("quantization_level", "")

                expires = m.get("expires_at", "")
                exp_str = ""
                if expires:
                    try:
                        exp_dt = datetime.fromisoformat(expires.replace("Z", "+00:00"))
                        now = datetime.now(exp_dt.tzinfo)
                        remaining = exp_dt - now
                        if remaining.total_seconds() > 0:
                            exp_str = f"expires in {int(remaining.total_seconds() / 60)}m"
                        else:
                            exp_str = "expiring"
                    except Exception:
                        pass

                lines.append(f"    [bold]{name}[/]")
                parts = []
                if params:
                    parts.append(params)
                if quant:
                    parts.append(quant)
                if vram > 0:
                    parts.append(f"VRAM {fmt_bytes(vram)}")
                if ram > 0:
                    parts.append(f"RAM {fmt_bytes(ram)}")
                if exp_str:
                    parts.append(f"[dim]{exp_str}[/]")
                if parts:
                    lines.append(f"    {' · '.join(parts)}")
                lines.append("")
        else:
            lines.append("  [dim]💤 No models loaded in memory[/]\n")

        models = status.get("models", [])
        running_names = {m.get("name") for m in running}
        installed = [m for m in models if m.get("name") not in running_names]

        if installed:
            lines.append(f"  📚 [bold]Installed ({len(models)} total):[/]")
            for m in installed:
                name = m.get("name", "?")
                size = m.get("size", 0)
                details = m.get("details", {})
                params = details.get("parameter_size", "")
                quant = details.get("quantization_level", "")
                is_cloud = bool(m.get("remote_model"))

                loc = "☁️" if is_cloud else "💾"
                parts = [loc]
                if params:
                    parts.append(params)
                if quant:
                    parts.append(quant)
                if not is_cloud:
                    parts.append(fmt_bytes(size))
                lines.append(f"    [dim]{name:<30}[/] {' · '.join(parts)}")
            lines.append("")

        self.query_one("#ollama-content", Static).update("\n".join(lines))


class AppPanel(Static):
    def compose(self) -> ComposeResult:
        yield Static(id="apps-content")

    def refresh_data(self) -> None:
        apps = STORE.get("apps") or []
        lines = ["[bold cyan]═══ 📦 Apps ═══[/]\n"]

        if not APPS:
            lines.append("  [dim]No apps configured. Add entries to APPS in app.py.[/]")
            self.query_one("#apps-content", Static).update("\n".join(lines))
            return

        for a in apps:
            if a.container_status == "running":
                icon = "🟢"
                status_str = "[green]running[/]"
            elif a.container_status == "stopped":
                icon = "🟡"
                status_str = "[yellow]stopped[/]"
            else:
                icon = "⚫"
                status_str = "[dim]missing[/]"

            head = f"  {icon}  [bold]{a.name:<24}[/] {status_str}"
            if a.uptime:
                head += f"  [dim]up {a.uptime}[/]"
            lines.append(head)

            if a.image:
                lines.append(f"       📦 {a.image}")

            mode_str = a.build_mode if a.build_mode != "none" else "[red]no Dockerfile/compose[/]"
            lines.append(f"       🔧 {mode_str}   📁 git: {a.git_state}")

            if a.tunnel:
                t_icon = "🟢" if a.tunnel_status == "active" else "🔴"
                lines.append(f"       ☁️  tunnel: {t_icon} {a.tunnel}")

            if not a.env_perms_ok:
                lines.append("       [red]⚠  env file perms looser than 600[/]")
            elif a.env_key_count is None:
                lines.append("       [dim]env: (no file)[/]")
            else:
                lines.append(f"       [dim]env: {a.env_key_count} keys[/]")
            lines.append("")

        lines.append(
            "[dim]  ⌨  [bold]R[/]=rebuild  [bold]E[/]=edit env  [bold]L[/]=logs[/]"
        )
        self.query_one("#apps-content", Static).update("\n".join(lines))


class TimerPanel(Static):
    def compose(self) -> ComposeResult:
        yield Static(id="timer-content")

    def refresh_data(self) -> None:
        timers = STORE.get("timers")
        lines = ["[bold cyan]═══ ⏲️  Systemd Timers ═══[/]\n"]
        if not timers:
            lines.append("  [dim]📭 No timers found[/]")
            self.query_one("#timer-content", Static).update("\n".join(lines))
            return

        for t in timers:
            state = t.get("ActiveState", "unknown")
            sub = t.get("SubState", "unknown")
            if state == "failed" or sub == "failed":
                icon = "🔴"
            elif state == "active":
                icon = "🟢"
            else:
                icon = "🟡"
            unit = t["unit"]
            desc = t.get("Description") or ""
            activates = t.get("activates") or "—"
            nxt = t.get("next") or "—"
            left = t.get("left") or ""
            last = t.get("last") or "—"
            passed = t.get("passed") or ""
            header = f"[bold]{desc}[/]" if desc else f"[bold]{unit}[/]"
            sub_unit = f"       [dim]{unit}[/]\n" if desc else ""
            lines.append(
                f"  {icon}  {header}\n"
                f"{sub_unit}"
                f"       ▶ [dim]{activates}[/]\n"
                f"       ⏭ next: {nxt}  [dim]({left})[/]\n"
                f"       ⏮ last: {last}  [dim]({passed})[/]\n"
            )
        lines.append("[dim]  ⌨  [bold]g[/]=logs[/]")
        self.query_one("#timer-content", Static).update("\n".join(lines))


# ─── Modal screens ───────────────────────────────────────────────────

class SelectorScreen(ModalScreen[str | None]):
    BINDINGS = [Binding("escape", "cancel", "Cancel")]

    def __init__(self, title: str, items: list[tuple[str, str]]) -> None:
        super().__init__()
        self.title_text = title
        self.items = items

    def compose(self) -> ComposeResult:
        with Container(id="selector-box"):
            yield Static(f"[bold]{self.title_text}[/]\n", id="selector-title")
            table = DataTable(id="selector-table")
            table.cursor_type = "row"
            table.add_columns("#", "Name")
            seen_keys = set()
            for i, (key, label) in enumerate(self.items, 1):
                k = key
                n = 1
                while k in seen_keys:
                    n += 1
                    k = f"{key}#{n}"
                seen_keys.add(k)
                table.add_row(str(i), label, key=k)
            yield table

    def on_data_table_row_selected(self, event: DataTable.RowSelected) -> None:
        self.dismiss(str(event.row_key.value))

    def action_cancel(self) -> None:
        self.dismiss(None)


class LogScreen(ModalScreen[None]):
    BINDINGS = [Binding("escape", "close", "Close"), Binding("q", "close", "Close")]

    def __init__(self, title: str, log_cmd: str) -> None:
        super().__init__()
        self.title_text = title
        self.log_cmd = log_cmd

    def compose(self) -> ComposeResult:
        with Container(id="log-box"):
            yield Static(
                f"[bold]{self.title_text}[/]  [dim]Press ESC or q to close[/]\n",
                id="log-title",
            )
            yield RichLog(id="log-view", wrap=True, highlight=True, markup=True)

    def on_mount(self) -> None:
        log_view = self.query_one("#log-view", RichLog)
        output = run_cmd(self.log_cmd, timeout=10)
        for line in output.splitlines():
            log_view.write(line)

    def action_close(self) -> None:
        self.dismiss(None)


class BuildScreen(ModalScreen[None]):
    BINDINGS = [Binding("escape", "close", "Close"), Binding("q", "close", "Close")]

    def __init__(self, title: str, app_cfg: "App") -> None:
        super().__init__()
        self.title_text = title
        self.app_cfg = app_cfg

    def compose(self) -> ComposeResult:
        with Container(id="log-box"):
            yield Static(
                f"[bold]{self.title_text}[/]  [dim]Press ESC or q to close[/]\n",
                id="log-title",
            )
            yield RichLog(id="log-view", wrap=True, highlight=False, markup=True)

    def on_mount(self) -> None:
        Thread(target=self._run, daemon=True).start()

    def _run(self) -> None:
        log_view = self.query_one("#log-view", RichLog)
        try:
            for line in rebuild_app(self.app_cfg):
                self.app.call_from_thread(log_view.write, line)
        finally:
            REBUILD_LOCK.release()
            self.app.call_from_thread(bg_fetch_cheap)
            self.app.call_from_thread(
                self.app.query_one("#apps-panel", AppPanel).refresh_data
            )

    def action_close(self) -> None:
        self.dismiss(None)


# ─── Main App ────────────────────────────────────────────────────────

class ServerTUI(App):
    TITLE = "ServerTUI"
    SUB_TITLE = socket.gethostname()

    CSS = """
    Screen {
        background: $surface;
    }

    #main-container {
        height: 1fr;
    }

    #left-col {
        width: 1fr;
        max-width: 50;
        min-width: 36;
        border-right: solid $primary-background;
        padding: 1 2;
    }

    #right-col {
        width: 2fr;
        padding: 1 2;
    }

    #tabs { height: 1fr; }
    TabPane { padding: 1 2; }

    /* Narrow layout (<90 cols): stack vertically */

    .narrow #main-container {
        layout: vertical;
        overflow-y: auto;
    }

    .narrow #left-col {
        width: 100%;
        max-width: 100%;
        height: auto;
        border-right: none;
        border-bottom: solid $primary-background;
        padding: 1 2;
    }

    .narrow #right-col {
        width: 100%;
        height: auto;
        padding: 1 2;
    }

    /* Modals */

    #selector-box {
        width: 60;
        max-width: 95%;
        height: auto;
        max-height: 80%;
        background: $surface;
        border: thick $primary;
        padding: 1 2;
        margin: 2 4;
    }

    #selector-table { height: auto; max-height: 20; }

    #log-box {
        width: 90%;
        height: 85%;
        background: $surface;
        border: thick $primary;
        padding: 1 2;
        margin: 1 2;
    }

    #log-view { height: 1fr; }

    SelectorScreen, LogScreen, BuildScreen { align: center middle; }
    """

    BINDINGS = [
        Binding("q", "quit", "Quit"),
        Binding("s", "tunnel_start", "Start tunnel"),
        Binding("t", "tunnel_stop", "Stop tunnel"),
        Binding("r", "tunnel_restart", "Restart tunnel"),
        Binding("l", "tunnel_logs", "Tunnel logs"),
        Binding("u", "docker_start", "Start container"),
        Binding("d", "docker_stop", "Stop container"),
        Binding("x", "docker_restart", "Restart container"),
        Binding("g", "timer_logs", "Timer logs"),
        Binding("1", "show_tab('tab-tunnels')", "Tunnels"),
        Binding("2", "show_tab('tab-docker')", "Docker"),
        Binding("3", "show_tab('tab-ollama')", "Ollama"),
        Binding("4", "show_tab('tab-timers')", "Timers"),
        Binding("5", "show_tab('tab-apps')", "Apps"),
        Binding("L", "app_logs", "App logs"),
        Binding("R", "app_rebuild", "Rebuild app"),
        Binding("E", "app_edit_env", "Edit env"),
        Binding("f", "refresh", "Refresh"),
    ]

    def compose(self) -> ComposeResult:
        yield Header()
        with Horizontal(id="main-container"):
            with Vertical(id="left-col"):
                yield SystemPanel(id="sys-panel")
            with TabbedContent(id="tabs", initial="tab-tunnels"):
                with TabPane("☁️  Tunnels", id="tab-tunnels"):
                    yield TunnelPanel(id="tunnel-panel")
                with TabPane("🐳 Docker", id="tab-docker"):
                    yield DockerPanel(id="docker-panel")
                with TabPane("🦙 Ollama", id="tab-ollama"):
                    yield OllamaPanel(id="ollama-panel")
                with TabPane("⏲️  Timers", id="tab-timers"):
                    yield TimerPanel(id="timer-panel")
                with TabPane("📦 Apps", id="tab-apps"):
                    yield AppPanel(id="apps-panel")
        yield Footer()

    def on_mount(self) -> None:
        # Prime CPU counter
        psutil.cpu_percent(interval=None)
        # Initial cheap fetch (instant)
        bg_fetch_cheap()
        self._render_ui()
        self._check_layout()
        # Kick off first expensive fetch in background
        self._start_bg_fetch()
        # Fast timer: refresh cheap data + re-render every 2s
        self.set_interval(2, self._tick_fast)
        # Slow timer: refresh expensive data (docker stats) every 15s
        self.set_interval(15, self._start_bg_fetch)

    def _tick_fast(self) -> None:
        """Quick refresh: system + tunnels only, then re-render all."""
        bg_fetch_cheap()
        self._render_ui()

    def _start_bg_fetch(self) -> None:
        """Kick off expensive fetches in a daemon thread."""
        thread = Thread(target=self._bg_worker, daemon=True)
        thread.start()

    def _bg_worker(self) -> None:
        """Runs in background thread — fetches Docker stats + Ollama."""
        bg_fetch_expensive()
        # Schedule a UI re-render on the main thread
        self.call_from_thread(self._render_ui)

    def _render_ui(self) -> None:
        """Re-render all panels from cached data. Always instant."""
        self.query_one("#sys-panel", SystemPanel).refresh_data()
        self.query_one("#tunnel-panel", TunnelPanel).refresh_data()
        self.query_one("#docker-panel", DockerPanel).refresh_data()
        self.query_one("#ollama-panel", OllamaPanel).refresh_data()
        self.query_one("#timer-panel", TimerPanel).refresh_data()
        self.query_one("#apps-panel", AppPanel).refresh_data()

    def on_resize(self) -> None:
        self._check_layout()

    def _check_layout(self) -> None:
        if self.size.width < 90:
            self.add_class("narrow")
        else:
            self.remove_class("narrow")

    # ── Tunnel actions ──

    def _tunnel_items(self) -> list[tuple[str, str]]:
        items = []
        tunnels = STORE.get("tunnels") or []
        for t in tunnels:
            state = t.get("ActiveState", "unknown")
            sub = t.get("SubState", "unknown")
            icon = "🟢" if (state == "active" and sub == "running") else "🔴"
            items.append((t["service"], f"{icon} {t['description']} ({t['domain']})"))
        return items

    def _on_tunnel_selected(self, action: str, service: str | None) -> None:
        if service is None:
            return
        systemctl_user(action, service)
        self.notify(f"{action.capitalize()}ed {service}")
        bg_fetch_cheap()
        self._render_ui()

    def action_tunnel_start(self) -> None:
        self.push_screen(
            SelectorScreen("Start Tunnel", self._tunnel_items()),
            lambda s: self._on_tunnel_selected("start", s),
        )

    def action_tunnel_stop(self) -> None:
        self.push_screen(
            SelectorScreen("Stop Tunnel", self._tunnel_items()),
            lambda s: self._on_tunnel_selected("stop", s),
        )

    def action_tunnel_restart(self) -> None:
        self.push_screen(
            SelectorScreen("Restart Tunnel", self._tunnel_items()),
            lambda s: self._on_tunnel_selected("restart", s),
        )

    def action_tunnel_logs(self) -> None:
        def callback(service: str | None) -> None:
            if service is None:
                return
            self.push_screen(LogScreen(
                f"Logs: {service}",
                f"journalctl --user -u {service} --no-pager -n 100",
            ))
        self.push_screen(SelectorScreen("View Tunnel Logs", self._tunnel_items()), callback)

    # ── Docker actions ──

    def _docker_items(self, status_filter: str | None = None) -> list[tuple[str, str]]:
        containers = STORE.get("docker") or []
        items = []
        for c in containers:
            if status_filter and c["status"] != status_filter:
                continue
            icon = "🟢" if c["status"] == "running" else "🔴"
            items.append((c["name"], f"{icon} {c['name']} ({c['status']})"))
        return items

    def _docker_action(self, action: str, name: str | None) -> None:
        if name is None:
            return
        try:
            client = docker.from_env()
            container = client.containers.get(name)
            getattr(container, action)()
            self.notify(f"{action.capitalize()}ed {name}")
        except Exception as e:
            self.notify(f"Error: {e}", severity="error")
        self._start_bg_fetch()

    def action_docker_start(self) -> None:
        items = self._docker_items(status_filter="exited")
        if not items:
            self.notify("No stopped containers", severity="warning")
            return
        self.push_screen(
            SelectorScreen("Start Container", items),
            lambda n: self._docker_action("start", n),
        )

    def action_docker_stop(self) -> None:
        items = self._docker_items(status_filter="running")
        if not items:
            self.notify("No running containers", severity="warning")
            return
        self.push_screen(
            SelectorScreen("Stop Container", items),
            lambda n: self._docker_action("stop", n),
        )

    def action_docker_restart(self) -> None:
        items = self._docker_items(status_filter="running")
        if not items:
            self.notify("No running containers", severity="warning")
            return
        self.push_screen(
            SelectorScreen("Restart Container", items),
            lambda n: self._docker_action("restart", n),
        )

    # ── Timer actions ──

    def _timer_items(self) -> list[tuple[str, str]]:
        items = []
        for t in STORE.get("timers") or []:
            state = t.get("ActiveState", "unknown")
            sub = t.get("SubState", "unknown")
            if state == "failed" or sub == "failed":
                icon = "🔴"
            elif state == "active":
                icon = "🟢"
            else:
                icon = "🟡"
            activates = t.get("activates") or "—"
            items.append((t["unit"], f"{icon} {t['unit']} → {activates}"))
        return items

    def action_timer_logs(self) -> None:
        def callback(unit: str | None) -> None:
            if unit is None:
                return
            target = unit
            for t in STORE.get("timers") or []:
                if t["unit"] == unit and t.get("activates"):
                    target = t["activates"]
                    break
            self.push_screen(LogScreen(
                f"Logs: {target}",
                f"journalctl --user -u {target} --no-pager -n 200",
            ))
        items = self._timer_items()
        if not items:
            self.notify("No timers found", severity="warning")
            return
        self.push_screen(SelectorScreen("View Timer Logs", items), callback)

    # ── App actions ──

    def _app_items(self) -> list[tuple[str, str]]:
        items = []
        for a in STORE.get("apps") or []:
            if a.container_status == "running":
                icon = "🟢"
            elif a.container_status == "stopped":
                icon = "🟡"
            else:
                icon = "⚫"
            items.append((a.name, f"{icon} {a.name} ({a.container_status})"))
        return items

    def action_app_logs(self) -> None:
        items = self._app_items()
        if not items:
            self.notify("No apps configured", severity="warning")
            return

        def callback(name: str | None) -> None:
            if name is None:
                return
            container = f"servertui-{name}"
            self.push_screen(LogScreen(
                f"Logs: {container}",
                f"docker logs --tail 200 {shlex.quote(container)}",
            ))

        self.push_screen(SelectorScreen("View App Logs", items), callback)

    def action_app_rebuild(self) -> None:
        items = self._app_items()
        if not items:
            self.notify("No apps configured", severity="warning")
            return

        def launch(name: str | None) -> None:
            if name is None:
                return
            app_cfg = next((a for a in APPS if a.name == name), None)
            if app_cfg is None:
                return
            info = next(
                (i for i in (STORE.get("apps") or []) if i.name == name), None,
            )

            def start() -> None:
                if not REBUILD_LOCK.acquire(blocking=False):
                    self.notify("A rebuild is already in progress", severity="warning")
                    return
                self.push_screen(BuildScreen(f"Rebuild: {name}", app_cfg))

            if info and info.git_state == "dirty":
                self.push_screen(
                    SelectorScreen(
                        f"{name}: repo is DIRTY — rebuild anyway?",
                        [("yes", "✅ yes, rebuild"), ("no", "❌ cancel")],
                    ),
                    lambda choice: start() if choice == "yes" else None,
                )
            else:
                start()

        self.push_screen(SelectorScreen("Rebuild App", items), launch)

    def action_app_edit_env(self) -> None:
        items = self._app_items()
        if not items:
            self.notify("No apps configured", severity="warning")
            return

        def on_selected(name: str | None) -> None:
            if name is None:
                return
            app_cfg = next((a for a in APPS if a.name == name), None)
            if app_cfg is None:
                return

            with self.suspend():
                changed, err = edit_env_file(app_cfg)

            bg_fetch_cheap()
            self._render_ui()

            if err:
                self.notify(err, severity="error")
                return
            if not changed:
                self.notify(f"{name}: env unchanged")
                return

            def maybe_restart(choice: str | None) -> None:
                if choice != "yes":
                    return
                container = f"servertui-{name}"
                try:
                    client = docker.from_env()
                    client.containers.get(container).restart()
                    self.notify(f"Restarted {container}")
                except docker.errors.NotFound:
                    self.notify(
                        f"{container} not running — press R to rebuild",
                        severity="warning",
                    )
                except Exception as e:
                    self.notify(f"Restart failed: {e}", severity="error")
                self._start_bg_fetch()

            self.push_screen(
                SelectorScreen(
                    f"{name}: env updated — restart container?",
                    [("yes", "✅ yes, restart now"), ("no", "❌ no")],
                ),
                maybe_restart,
            )

        self.push_screen(SelectorScreen("Edit App Env", items), on_selected)

    def action_show_tab(self, tab_id: str) -> None:
        self.query_one("#tabs", TabbedContent).active = tab_id

    def action_refresh(self) -> None:
        bg_fetch_cheap()
        self._render_ui()
        self._start_bg_fetch()
        self.notify("Refreshed")


if __name__ == "__main__":
    app = ServerTUI()
    app.run()
