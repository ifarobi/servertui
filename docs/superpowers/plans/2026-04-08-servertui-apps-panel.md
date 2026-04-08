# ServerTUI Apps Panel Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add an "Apps" tab to ServerTUI that lets the user manually rebuild locally-cloned app repos (git pull → docker build → restart) and edit per-app env files via `$EDITOR`, all from inside the TUI.

**Architecture:** All changes land in the single-file `app.py`, following the existing patterns: an `App` dataclass and `APPS` list next to `TUNNELS`; an `AppInfo` state type added to `DataStore`; a cheap synchronous fetcher populating it; a new `AppPanel(Static)` rendering from the store; a one-shot background thread for each rebuild that streams output into a new `BuildScreen` modal; and `app.suspend()` for the `$EDITOR` flow. No new files, no tests (consistent with the rest of the project).

**Tech Stack:** Python 3.11+, Textual, `docker` SDK (already a dep), `subprocess` for git/compose, `psutil` (already a dep). No new dependencies.

**Spec:** `docs/superpowers/specs/2026-04-08-servertui-apps-panel-design.md`

**Verification convention:** Since the project has no test suite, each task's verification is a manual check: run `./run.sh`, look at the Apps tab, exercise the new action. Tasks end with a commit using conventional-commit style, matching recent history.

---

## File structure

- **Modify only:** `/home/ifr/sandbox/servertui/app.py`
- **Runtime dirs created by code at first use:** `~/.config/servertui/env/` (mode 700)

Every task is a set of edits to `app.py`. Line numbers in the plan reference the file *as of this plan's writing* (962 lines); use the anchor strings in each Edit, not the numbers, when applying.

---

## Task 1: Add `App` / `AppInfo` types and `APPS` config list

**Files:**
- Modify: `app.py` — config section (around line 37, after `TUNNELS`)

- [ ] **Step 1: Add `dataclass` and `Path` imports**

At the top of `app.py`, the import block currently has:

```python
from datetime import datetime
from threading import Thread, Lock
```

Add `dataclass` and `Path` imports immediately after:

```python
from dataclasses import dataclass, field
from pathlib import Path
```

- [ ] **Step 2: Add `App` dataclass and `APPS` list after `TUNNELS`**

Find this block (around line 35-39):

```python
## Tunnels are auto-discovered from `cloudflared-*.service` user units.
## Add entries here only to override description/domain or pin ordering.
TUNNELS: list[dict] = []

OLLAMA_BASE = "http://localhost:11434"
```

Insert the following immediately after `TUNNELS: list[dict] = []` and before `OLLAMA_BASE`:

```python

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
```

- [ ] **Step 3: Add `AppInfo` state type**

Directly below the `APPS` / `ENV_DIR` block, add the snapshot type that the fetcher will produce and the panel will read:

```python
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
```

- [ ] **Step 4: Verify the file still parses**

Run: `python -c "import ast; ast.parse(open('app.py').read()); print('ok')"`
Expected: `ok`

- [ ] **Step 5: Commit**

```bash
git add app.py
git commit -m "Add App/AppInfo dataclasses and APPS config list"
```

---

## Task 2: Extend `DataStore` with an `apps` slot

**Files:**
- Modify: `app.py` — `DataStore.__init__` (around line 141-149)

- [ ] **Step 1: Add `apps` key to the store's initial dict**

Find this block:

```python
    def __init__(self):
        self._lock = Lock()
        self._data = {
            "system": {},
            "tunnels": [],
            "timers": [],
            "docker": [],
            "ollama": {},
        }
```

Replace with:

```python
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
```

- [ ] **Step 2: Verify**

Run: `python -c "import ast; ast.parse(open('app.py').read()); print('ok')"`
Expected: `ok`

- [ ] **Step 3: Commit**

```bash
git add app.py
git commit -m "Add apps slot to DataStore"
```

---

## Task 3: Implement the apps fetcher

**Files:**
- Modify: `app.py` — add `DataStore.fetch_apps` method, and call it from `bg_fetch_cheap()`

This fetcher must be cheap (sub-100ms even with 10 apps) because it runs on the 2s foreground tick. Every data source it touches is already cheap: a stat on the env file, two `git` commands on a local repo, an in-memory lookup of the tunnel list, and one `docker inspect` per app.

- [ ] **Step 1: Add a small helper for env file inspection**

Find the helpers section (around line 111, just above `get_uptime`). Add this helper near the other file/number helpers — insert it immediately before `def get_uptime()`:

```python
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
```

- [ ] **Step 2: Add `fetch_apps` method on `DataStore`**

Find the end of `fetch_ollama` in `DataStore` (search for `def fetch_ollama`). Immediately after that method ends (before the `def bg_fetch_expensive()` top-level function), add a new method inside `DataStore`:

```python
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
```

- [ ] **Step 3: Wire `fetch_apps` into the cheap fetcher**

Find `bg_fetch_cheap`:

```python
def bg_fetch_cheap():
    """Fetch quick data (system + tunnels) — can run on main timer."""
    STORE.fetch_system()
    STORE.fetch_tunnels()
    STORE.fetch_timers()
```

Replace with:

```python
def bg_fetch_cheap():
    """Fetch quick data (system + tunnels) — can run on main timer."""
    STORE.fetch_system()
    STORE.fetch_tunnels()
    STORE.fetch_timers()
    STORE.fetch_apps()
```

Note: `fetch_apps` reads from `self._data["tunnels"]`, so it must run *after* `fetch_tunnels`. The order above is correct.

- [ ] **Step 4: Smoke-test the fetcher by hand**

Run: `python -c "from app import STORE, bg_fetch_cheap; bg_fetch_cheap(); print(STORE.get('apps'))"`
Expected: `[]` (since `APPS` is empty).

- [ ] **Step 5: Commit**

```bash
git add app.py
git commit -m "Add fetch_apps for apps panel state snapshot"
```

---

## Task 4: Add `AppPanel` widget and wire it into the tabbed layout

**Files:**
- Modify: `app.py` — add `AppPanel` class; add a new `TabPane` in `ServerTUI.compose`; add panel to `_render_ui`; add `5` key binding; renumber existing tabs if needed (they can stay as-is).

- [ ] **Step 1: Add `AppPanel` class**

Locate `class TimerPanel(Static):` (around line 549). Immediately above it (so `AppPanel` sits next to the other right-column panels), add:

```python
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
```

- [ ] **Step 2: Import `AppPanel` nowhere — it's same-file. Add `TabPane` in `compose`**

Find this block in `ServerTUI.compose` (around line 754-763):

```python
            with TabbedContent(id="tabs", initial="tab-tunnels"):
                with TabPane("☁️  Tunnels", id="tab-tunnels"):
                    yield TunnelPanel(id="tunnel-panel")
                with TabPane("🐳 Docker", id="tab-docker"):
                    yield DockerPanel(id="docker-panel")
                with TabPane("🦙 Ollama", id="tab-ollama"):
                    yield OllamaPanel(id="ollama-panel")
                with TabPane("⏲️  Timers", id="tab-timers"):
                    yield TimerPanel(id="timer-panel")
```

Replace with:

```python
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
```

- [ ] **Step 3: Add the panel to `_render_ui`**

Find:

```python
    def _render_ui(self) -> None:
        """Re-render all panels from cached data. Always instant."""
        self.query_one("#sys-panel", SystemPanel).refresh_data()
        self.query_one("#tunnel-panel", TunnelPanel).refresh_data()
        self.query_one("#docker-panel", DockerPanel).refresh_data()
        self.query_one("#ollama-panel", OllamaPanel).refresh_data()
        self.query_one("#timer-panel", TimerPanel).refresh_data()
```

Replace with:

```python
    def _render_ui(self) -> None:
        """Re-render all panels from cached data. Always instant."""
        self.query_one("#sys-panel", SystemPanel).refresh_data()
        self.query_one("#tunnel-panel", TunnelPanel).refresh_data()
        self.query_one("#docker-panel", DockerPanel).refresh_data()
        self.query_one("#ollama-panel", OllamaPanel).refresh_data()
        self.query_one("#timer-panel", TimerPanel).refresh_data()
        self.query_one("#apps-panel", AppPanel).refresh_data()
```

- [ ] **Step 4: Add the `5` tab shortcut**

Find the `BINDINGS` list in `ServerTUI`:

```python
        Binding("1", "show_tab('tab-tunnels')", "Tunnels"),
        Binding("2", "show_tab('tab-docker')", "Docker"),
        Binding("3", "show_tab('tab-ollama')", "Ollama"),
        Binding("4", "show_tab('tab-timers')", "Timers"),
```

Add immediately after:

```python
        Binding("5", "show_tab('tab-apps')", "Apps"),
```

- [ ] **Step 5: Manual verification**

Run: `./run.sh`
Expected:
- TUI launches without error.
- A new "📦 Apps" tab is visible.
- Pressing `5` switches to it.
- With `APPS = []`, the tab shows: `No apps configured. Add entries to APPS in app.py.`
- Quit with `q`.

- [ ] **Step 6: Commit**

```bash
git add app.py
git commit -m "Add Apps tab with AppPanel widget"
```

---

## Task 5: Add app selector helper and `L` action for app logs

Reuse the existing `SelectorScreen` + `LogScreen` pattern. `L` is currently bound to `tunnel_logs`; to avoid a conflict, the Apps tab `L` shortcut is a new binding `action_app_logs` triggered only via the footer button — but since Textual bindings are global, we give this a different key: **use capital `L` via a footer label, but the actual key stays `L` with a `show=True` binding; the tunnel `l` binding stays lowercase.** Python/Textual bindings are case-sensitive, so `l` and `L` (shift+l) are distinct.

**Files:**
- Modify: `app.py` — add `_app_items` and `action_app_logs`, add binding

- [ ] **Step 1: Add `_app_items` and `action_app_logs` method**

Find the existing `# ── Timer actions ──` section (around line 914). Immediately after `action_timer_logs` ends and before `def action_show_tab`, add a new section:

```python
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
```

- [ ] **Step 2: Add the `L` (shift+l) binding**

Find the `BINDINGS` list again and add immediately after the `Binding("5", ...)` line from Task 4:

```python
        Binding("L", "app_logs", "App logs"),
```

- [ ] **Step 3: Manual verification**

Run: `./run.sh`
Expected:
- `L` (capital) is visible in the footer as "App logs".
- Pressing `L` with `APPS = []` shows a warning notification "No apps configured".
- Pressing lowercase `l` still opens the tunnel log selector.

- [ ] **Step 4: Commit**

```bash
git add app.py
git commit -m "Add app log viewer action"
```

---

## Task 6: Add `BuildScreen` modal and `R` rebuild action

**Files:**
- Modify: `app.py` — add `BuildScreen` class, `rebuild_app` generator, `action_app_rebuild`, binding, and a global rebuild lock.

- [ ] **Step 1: Add a module-level rebuild lock**

Find this block near the top (around line 15):

```python
from threading import Thread, Lock
```

That import is already present. Find the line near `STORE = DataStore()` (search for it in the file). Add immediately after:

```python
REBUILD_LOCK = Lock()
```

- [ ] **Step 2: Add the `rebuild_app` generator**

Add this as a top-level function immediately below `bg_fetch_cheap` (around line 344):

```python
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
```

- [ ] **Step 3: Add `BuildScreen` modal**

Find `class LogScreen(ModalScreen[None]):` (around line 624). Immediately after its closing `def action_close` method ends (before the `# ─── Main App ───` comment), add:

```python
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
```

- [ ] **Step 4: Add CSS entry for `BuildScreen`**

Find the last CSS line before the closing `"""`:

```python
    SelectorScreen, LogScreen { align: center middle; }
    """
```

Replace with:

```python
    SelectorScreen, LogScreen, BuildScreen { align: center middle; }
    """
```

- [ ] **Step 5: Add `action_app_rebuild` method**

Inside the `# ── App actions ──` section added in Task 5 (immediately after `action_app_logs`), add:

```python
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
    ```

- [ ] **Step 6: Add the `R` (shift+r) binding**

Find the BINDINGS list and add after the `Binding("L", ...)` line:

```python
        Binding("R", "app_rebuild", "Rebuild app"),
```

**Important:** lowercase `r` is already bound to `tunnel_restart`. Shift+R is distinct in Textual and is what the spec/user expects.

- [ ] **Step 7: Manual verification with a throwaway app**

1. Create a test repo:
   ```bash
   mkdir -p /tmp/servertui-hello && cd /tmp/servertui-hello
   git init -q && git commit -q --allow-empty -m init
   cat > Dockerfile <<'EOF'
   FROM alpine
   CMD ["sh", "-c", "echo hello from servertui && sleep 3600"]
   EOF
   git add Dockerfile && git commit -q -m add
   ```
2. Edit `app.py` to add one entry temporarily:
   ```python
   APPS: list[App] = [
       App(name="hello", repo_path=Path("/tmp/servertui-hello")),
   ]
   ```
3. Run: `./run.sh`
4. Press `5` — the hello app appears with status `missing`.
5. Press `R`, select `hello`. BuildScreen opens, streams git + docker build + docker run output, ends with `[exit 0]`.
6. Close with ESC. Panel now shows `hello` as `running`.
7. Press `L`, select `hello`, see `hello from servertui` in logs.
8. Quit. Revert `APPS = []` **before committing**.

- [ ] **Step 8: Clean up test container and commit**

```bash
docker rm -f servertui-hello 2>/dev/null || true
docker rmi servertui-hello 2>/dev/null || true
git add app.py
git commit -m "Add rebuild action with BuildScreen and rebuild lock"
```

---

## Task 7: Add `E` env file edit action with `$EDITOR` suspend

**Files:**
- Modify: `app.py` — add `edit_env_file` helper, `action_app_edit_env`, binding

- [ ] **Step 1: Add `edit_env_file` top-level helper**

Add immediately below the `rebuild_app` generator (added in Task 6):

```python
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
```

- [ ] **Step 2: Add `action_app_edit_env` method**

Inside `# ── App actions ──`, below `action_app_rebuild`, add:

```python
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
```

- [ ] **Step 3: Add the `E` (shift+e) binding**

Find the BINDINGS list and add after the `Binding("R", ...)` line from Task 6:

```python
        Binding("E", "app_edit_env", "Edit env"),
```

- [ ] **Step 4: Manual verification**

1. Ensure `$EDITOR` is set to a terminal editor you have installed (e.g. `export EDITOR=nano`).
2. Temporarily re-add the `hello` app from Task 6.
3. Run: `./run.sh`
4. Press `5` then `E`, select `hello`. ServerTUI suspends, nano opens on `~/.config/servertui/env/hello.env`.
5. Add a line `FOO=bar`, save, exit. ServerTUI resumes. A prompt asks to restart.
6. Select `no`. Panel now shows `env: 1 keys`.
7. Verify perms: `stat -c %a ~/.config/servertui/env/hello.env` → `600`.
8. Manually loosen perms: `chmod 644 ~/.config/servertui/env/hello.env`. Within 2s the panel shows `⚠ env file perms looser than 600`. Press `E` again and confirm it refuses to open with an error. Restore with `chmod 600`.
9. Revert `APPS = []` before committing.

- [ ] **Step 5: Commit**

```bash
git add app.py
git commit -m "Add env file edit action with \$EDITOR suspend"
```

---

## Task 8: Update docstrings and CLAUDE.md reference

**Files:**
- Modify: `app.py` — module docstring
- Modify: `CLAUDE.md` — keybindings line

- [ ] **Step 1: Update the module docstring**

Find the top of `app.py`:

```python
"""
ServerTUI — Local server dashboard for managing Cloudflare tunnels,
Docker containers, and monitoring system resources.
"""
```

Replace with:

```python
"""
ServerTUI — Local server dashboard for managing Cloudflare tunnels,
Docker containers, apps (local repos), and monitoring system resources.
"""
```

- [ ] **Step 2: Update the keybindings line in `CLAUDE.md`**

Find this line:

```
`1-4` switch tab · `s/t/r/l` tunnel start/stop/restart/logs · `u/d/x` container start/stop/restart · `g` timer logs · `f` force refresh · `q` quit
```

Replace with:

```
`1-5` switch tab · `s/t/r/l` tunnel start/stop/restart/logs · `u/d/x` container start/stop/restart · `g` timer logs · `R/E/L` app rebuild/edit-env/logs · `f` force refresh · `q` quit
```

- [ ] **Step 3: Commit**

```bash
git add app.py CLAUDE.md
git commit -m "Document apps panel in module docstring and CLAUDE.md"
```

---

## Self-review against spec

- **Apps tab with per-row state (container, image, uptime, tunnel, git, env keys):** Task 4 (`AppPanel.refresh_data`) + Task 3 (`fetch_apps`). ✅
- **`App` dataclass + `APPS` list + `servertui-<name>` convention:** Task 1. ✅
- **Manual rebuild `R` (git pull → build → restart), with dockerfile and compose auto-detection:** Task 6 (`rebuild_app` + `action_app_rebuild`). ✅
- **Build failure leaves existing container untouched:** Task 6 — `docker rm -f` only runs after `docker build` returns 0. ✅
- **Single global rebuild lock:** Task 6 (`REBUILD_LOCK`, released in `BuildScreen._run` `finally`). ✅
- **Dirty-tree confirm prompt:** Task 6 — selector "rebuild anyway?" prompt. ✅
- **Streaming build output modal:** Task 6 (`BuildScreen`). ✅
- **Env file path `~/.config/servertui/env/<name>.env`, perms 600:** Tasks 3 + 7. ✅
- **Env perms check refuses looser than 600, warning in panel + refusal in rebuild + refusal in edit:** Task 3 (panel) + Task 6 (rebuild refusal) + Task 7 (edit refusal). ✅
- **`E` suspends TUI, launches `$EDITOR`, resumes, offers restart:** Task 7. ✅
- **`L` reuses `LogScreen` against `docker logs`:** Task 5. ✅
- **Only container names are shown, env values never logged or rendered:** Confirmed — `AppPanel` only renders `env_key_count`; `inspect_env_file` never retains values. ✅
- **No auto-deploy / webhooks / rollback / remote repos / secrets beyond env:** None of those features appear in any task. ✅
- **Docstring + CLAUDE.md updated:** Task 8. ✅

**Placeholder scan:** no TODO/TBD/"similar to" references; every step shows the code it changes.

**Type consistency:** `App` / `AppInfo` field names used in Tasks 3, 4, 5, 6, 7 match Task 1 exactly. `servertui-<name>` container naming is used consistently across Tasks 3, 5, 6, 7.
