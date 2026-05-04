# Multi-`.env` Files per App — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the single canonical `~/.config/servertui/env/<name>.env` with a per-app **directory** `~/.config/servertui/env/<name>/` holding arbitrary `.env*` files. In compose mode, ServerTUI symlinks each managed file into the repo so `env_file:` entries and `${VAR}` interpolation just work.

**Architecture:** Pure helpers + symlink orchestration in `core.py`; file picker added between app picker and editor in `tui.py`; `I=import` retargeted from one canonical to per-source-named files. No `apps.json` schema change. Auto-migration is lazy, idempotent, on first read or first edit.

**Tech Stack:** Python 3.11+, Textual. No new dependencies.

**Branch:** `multi-env-files` cut from current `main` (post `bf52ba9 Bump version to 0.2.1`).

**Spec:** `docs/superpowers/specs/2026-05-04-multi-env-files-design.md`

---

## File Structure

- **Modify `src/servertui/core.py`:**
  - Add helpers `env_dir_for`, `migrate_legacy_env`, `list_env_files`, `inspect_env_dir`, `wire_env_into_repo` near `inspect_env_file` (line 172).
  - Update `fetch_app_status` (line 562) to use `migrate_legacy_env` + `inspect_env_dir`.
  - Refactor `rebuild_app` (line 600): per-file perms loop, multi `--env-file` in dockerfile mode, `wire_env_into_repo` in compose mode (replaces the yellow "not auto-wired" warning at line 720).
- **Modify `src/servertui/tui.py`:**
  - Import the new helpers (line 22 cluster).
  - Refactor `edit_env_file` (line 323) to take a `filename` parameter and operate on the per-app dir.
  - Refactor `action_app_edit_env` (line 1284) to insert a file-picker step.
  - Refactor `action_app_import_env` (line 1337): destination becomes `env_dir_for(name) / source_name`.
- **Modify `pyproject.toml` and `src/servertui/__init__.py`** — version bump to 0.3.0.
- **Modify `README.md` and `CLAUDE.md`** — env-files section and keybindings line as needed.

Each task ends with a smoke check + commit.

---

## Task 1: Core helpers — `env_dir_for` + `migrate_legacy_env` + `list_env_files` + `inspect_env_dir`

**Files:**
- Modify: `src/servertui/core.py` — insert after `inspect_env_file` (line 192 closing `return`), before `def parse_env_file`.

- [ ] **Step 1: Cut branch**

```bash
git fetch origin
git switch -c multi-env-files origin/main
```

- [ ] **Step 2: Add the four helpers**

Insert immediately after the `return` on line 192 of `inspect_env_file`:

```python
def env_dir_for(app_name: str) -> Path:
    """Per-app env directory under ENV_DIR."""
    return ENV_DIR / app_name


def migrate_legacy_env(app_name: str) -> None:
    """One-shot: ENV_DIR/<name>.env (regular file) -> ENV_DIR/<name>/.env.

    Silent no-op if already migrated, target exists, or any OSError.
    Safe to call from hot paths (fetch_app_status, edit_env_file)."""
    legacy = ENV_DIR / f"{app_name}.env"
    if not legacy.is_file() or legacy.is_symlink():
        return
    new_dir = env_dir_for(app_name)
    target = new_dir / ".env"
    try:
        new_dir.mkdir(parents=True, exist_ok=True)
        os.chmod(new_dir, 0o700)
        if target.exists():
            print(
                f"[servertui] migrate skipped: both {legacy} and {target} exist; "
                "leaving legacy in place — please resolve manually",
                file=sys.stderr,
            )
            return
        legacy.rename(target)
    except OSError as e:
        print(f"[servertui] migrate {legacy} -> {target} failed: {e}",
              file=sys.stderr)


def list_env_files(app_name: str) -> list[Path]:
    """Sorted .env* regular files in the app's env dir. Empty if dir missing.

    Excludes symlinks and subdirectories. Order: filename-sorted so .env
    precedes .env.production (Docker applies later --env-file on top of earlier)."""
    d = env_dir_for(app_name)
    if not d.is_dir():
        return []
    return sorted(
        p for p in d.iterdir()
        if p.is_file() and not p.is_symlink() and p.name.startswith(".env")
    )


def inspect_env_dir(app_name: str) -> tuple[int | None, bool]:
    """Aggregate (key_count, perms_ok) across all managed env files for an app.

    key_count is None when no managed files exist (matches inspect_env_file's
    "missing" semantic); otherwise it is the sum across files. perms_ok is the
    AND across files."""
    files = list_env_files(app_name)
    if not files:
        return (None, True)
    total = 0
    perms_ok = True
    any_readable = False
    for p in files:
        c, ok = inspect_env_file(p)
        perms_ok = perms_ok and ok
        if c is not None:
            total += c
            any_readable = True
    return (total if any_readable else None, perms_ok)
```

- [ ] **Step 3: Smoke test the helpers**

```bash
.venv/bin/python - <<'PY'
import os, tempfile, pathlib, sys
sys.path.insert(0, "src")
from servertui import core
tmp = pathlib.Path(tempfile.mkdtemp())
core.ENV_DIR = tmp  # rebind for test
# Seed a legacy file
legacy = tmp / "myapp.env"
legacy.write_text("FOO=bar\nBAZ=qux\n")
os.chmod(legacy, 0o600)
# Migrate
core.migrate_legacy_env("myapp")
assert not legacy.exists(), "legacy still present"
new = tmp / "myapp" / ".env"
assert new.is_file(), "migrated file missing"
assert (new.stat().st_mode & 0o777) == 0o600, "perms changed"
# list_env_files + inspect_env_dir
files = core.list_env_files("myapp")
assert [p.name for p in files] == [".env"], files
count, ok = core.inspect_env_dir("myapp")
assert (count, ok) == (2, True), (count, ok)
# Add a second file
prod = tmp / "myapp" / ".env.production"
prod.write_text("API_URL=x\n")
os.chmod(prod, 0o600)
files = core.list_env_files("myapp")
assert [p.name for p in files] == [".env", ".env.production"], files
count, ok = core.inspect_env_dir("myapp")
assert (count, ok) == (3, True), (count, ok)
# Idempotent migrate
core.migrate_legacy_env("myapp")  # no-op
assert new.is_file()
# Missing app
assert core.list_env_files("nope") == []
assert core.inspect_env_dir("nope") == (None, True)
print("TASK1 OK")
PY
```

Expected: prints `TASK1 OK`. No tracebacks.

- [ ] **Step 4: Commit**

```bash
git add src/servertui/core.py
git commit -m "Add per-app env directory helpers and lazy legacy migration"
```

---

## Task 2: Core helper — `wire_env_into_repo`

**Files:**
- Modify: `src/servertui/core.py` — append after `inspect_env_dir` (the function added in Task 1).

- [ ] **Step 1: Add the function**

```python
def wire_env_into_repo(app_name: str, repo_path: Path) -> tuple[bool, list[str]]:
    """Symlink every managed env file into <repo_path>/<filename>.

    Returns (ok, messages). On any abort condition `ok` is False and the
    last message explains why; on success `ok` is True and messages contain
    one [dim] line per wired file. Idempotent: existing correct symlinks
    are left untouched.

    Aborts on:
    - filename outside the .env* allowlist (defensive — list_env_files
      already filters but we recheck here)
    - target path is a real (non-symlink) git-tracked file
    - any OSError during symlink creation
    """
    messages: list[str] = []
    for ef in list_env_files(app_name):
        if not ef.name.startswith(".env") or "/" in ef.name:
            messages.append(f"[red]refusing unsafe filename: {ef.name}[/]")
            return (False, messages)
        link = repo_path / ef.name
        # Refuse to shadow a git-tracked real file.
        if not link.is_symlink():
            try:
                tracked = subprocess.run(
                    ["git", "ls-files", "--error-unmatch", ef.name],
                    cwd=str(repo_path), capture_output=True, text=True,
                    timeout=5,
                )
            except (OSError, subprocess.TimeoutExpired) as e:
                messages.append(f"[red]git ls-files failed for {ef.name}: {e}[/]")
                return (False, messages)
            if tracked.returncode == 0 and link.exists():
                messages.append(
                    f"[red]{link} is tracked in git -- refusing to overwrite "
                    "with managed env[/]"
                )
                messages.append(
                    f"[red]either untrack it (git rm --cached {ef.name}) "
                    "or rename the managed file[/]"
                )
                return (False, messages)
        # Idempotent symlink.
        try:
            if link.is_symlink() or link.exists():
                if link.is_symlink() and link.resolve() == ef.resolve():
                    messages.append(f"[dim]env wired (unchanged): {ef.name}[/]")
                    continue
                link.unlink()
            link.symlink_to(ef)
            messages.append(f"[dim]wired env: {ef.name} -> {ef}[/]")
        except OSError as e:
            messages.append(f"[red]could not symlink {link}: {e}[/]")
            return (False, messages)
    return (True, messages)
```

- [ ] **Step 2: Smoke test (uses a real git repo)**

```bash
.venv/bin/python - <<'PY'
import os, subprocess, tempfile, pathlib, sys
sys.path.insert(0, "src")
from servertui import core
tmp = pathlib.Path(tempfile.mkdtemp())
core.ENV_DIR = tmp
# Two managed files
(tmp / "myapp").mkdir()
for n in (".env", ".env.production"):
    p = tmp / "myapp" / n
    p.write_text(f"# {n}\n")
    os.chmod(p, 0o600)
# Repo
repo = tmp / "repo"
repo.mkdir()
subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
subprocess.run(["git", "config", "user.email", "t@t"], cwd=repo, check=True)
subprocess.run(["git", "config", "user.name", "t"], cwd=repo, check=True)
# Case 1: clean wire
ok, msgs = core.wire_env_into_repo("myapp", repo)
assert ok, msgs
assert (repo / ".env").is_symlink()
assert (repo / ".env.production").is_symlink()
print("CASE1 OK")
# Case 2: idempotent
ok, msgs = core.wire_env_into_repo("myapp", repo)
assert ok, msgs
assert any("unchanged" in m for m in msgs), msgs
print("CASE2 OK")
# Case 3: tracked file collision
(repo / ".env").unlink()  # remove symlink
(repo / ".env").write_text("REAL=tracked\n")
subprocess.run(["git", "add", ".env"], cwd=repo, check=True)
subprocess.run(["git", "commit", "-q", "-m", "track"], cwd=repo, check=True)
ok, msgs = core.wire_env_into_repo("myapp", repo)
assert not ok, msgs
assert any("tracked in git" in m for m in msgs), msgs
print("CASE3 OK")
print("TASK2 OK")
PY
```

Expected: `CASE1 OK`, `CASE2 OK`, `CASE3 OK`, `TASK2 OK`.

- [ ] **Step 3: Commit**

```bash
git add src/servertui/core.py
git commit -m "Add wire_env_into_repo for compose-mode env file symlinking"
```

---

## Task 3: Refactor `fetch_app_status` to use the directory model

**Files:**
- Modify: `src/servertui/core.py` — line 562 vicinity.

- [ ] **Step 1: Replace the env inspection lines**

Find:

```python
        env_path = ENV_DIR / f"{app.name}.env"
        env_count, env_perms_ok = inspect_env_file(env_path)
```

Replace with:

```python
        migrate_legacy_env(app.name)
        env_count, env_perms_ok = inspect_env_dir(app.name)
```

(The migration is lazy and idempotent; placing it here ensures the status panel never displays a stale legacy file.)

- [ ] **Step 2: Sanity check**

```bash
.venv/bin/python -c "
import sys; sys.path.insert(0, 'src')
from servertui.core import fetch_app_status, load_apps
apps = load_apps()
out = fetch_app_status(apps, {})
for a in out:
    print(a.name, 'env_keys=', a.env_key_count, 'perms_ok=', a.env_perms_ok)
"
```

Expected: prints one line per configured app. If you have a legacy `~/.config/servertui/env/coloring.env`, after this command it should be gone, replaced by `~/.config/servertui/env/coloring/.env`. Verify:

```bash
ls -la ~/.config/servertui/env/
ls -la ~/.config/servertui/env/coloring/ 2>/dev/null
```

- [ ] **Step 3: Commit**

```bash
git add src/servertui/core.py
git commit -m "Wire fetch_app_status to per-app env directory + lazy migration"
```

---

## Task 4: Refactor `rebuild_app`

**Files:**
- Modify: `src/servertui/core.py` — `rebuild_app` body starting line 600.

- [ ] **Step 1: Replace the env_path setup at function entry**

Find (near line 605):

```python
    container = f"servertui-{app.name}"
    env_path = ENV_DIR / f"{app.name}.env"
```

Replace with:

```python
    container = f"servertui-{app.name}"
    migrate_legacy_env(app.name)
    env_files = list_env_files(app.name)
```

- [ ] **Step 2: Replace the per-app perms check**

Find the block (around line 626 after the build-mode detection):

```python
    if env_path.exists():
        st = env_path.stat()
        if (st.st_mode & 0o777) != 0o600:
            yield f"[red]env file perms looser than 600: {env_path}[/]"
            yield "[red]fix with: chmod 600 {}[/]".format(env_path)
            yield "[exit 1]"
            return
```

Replace with:

```python
    for ef in env_files:
        st = ef.stat()
        if (st.st_mode & 0o777) != 0o600:
            yield f"[red]env file perms looser than 600: {ef}[/]"
            yield "[red]fix with: chmod 600 {}[/]".format(ef)
            yield "[exit 1]"
            return
```

- [ ] **Step 3: Replace the dockerfile-mode --env-file block**

Find (near line 700):

```python
        if env_path.exists():
            run_cmd_list += ["--env-file", str(env_path)]
        run_cmd_list.append(image_tag)
```

Replace with:

```python
        for ef in env_files:
            run_cmd_list += ["--env-file", str(ef)]
        run_cmd_list.append(image_tag)
```

- [ ] **Step 4: Replace the compose-mode warning with `wire_env_into_repo`**

Find (near line 720):

```python
    if env_path.exists():
        yield (
            "[yellow]note: compose mode -- ServerTUI's env file is NOT auto-wired.[/]\n"
            "[yellow]Reference it in compose.yml via `env_file: "
            f"{env_path}` or `${{VAR}}` interpolation.[/]"
        )
    cmd = ["docker", "compose", "-f", str(compose_file), "up", "-d", "--build"]
```

Replace with:

```python
    if env_files:
        ok, msgs = wire_env_into_repo(app.name, app.repo_path)
        for m in msgs:
            yield m
        if not ok:
            yield "[exit 1]"
            return
    cmd = ["docker", "compose", "-f", str(compose_file), "up", "-d", "--build"]
```

- [ ] **Step 5: Static check**

```bash
.venv/bin/python -c "import ast; ast.parse(open('src/servertui/core.py').read()); print('syntax ok')"
.venv/bin/python -c "import sys; sys.path.insert(0,'src'); from servertui.core import rebuild_app, wire_env_into_repo; print('imports ok')"
```

Expected: `syntax ok`, `imports ok`.

- [ ] **Step 6: Commit**

```bash
git add src/servertui/core.py
git commit -m "Wire rebuild_app to per-app env directory and auto-symlink in compose mode"
```

---

## Task 5: Update `tui.py` imports + refactor `edit_env_file`

**Files:**
- Modify: `src/servertui/tui.py` — import block (line 22) and `edit_env_file` (line 323).

- [ ] **Step 1: Extend the imports**

Find:

```python
from servertui.core import (
    App as AppConfig,
    AppInfo,
    ENV_DIR,
    clone_if_missing,
    docker_action,
    docker_container_stats,
    fetch_app_status,
    fmt_bytes,
    load_apps,
    merge_env_keys,
    parse_env_file,
    rebuild_app,
    run_cmd,
)
```

Replace with:

```python
from servertui.core import (
    App as AppConfig,
    AppInfo,
    ENV_DIR,
    clone_if_missing,
    docker_action,
    docker_container_stats,
    env_dir_for,
    fetch_app_status,
    fmt_bytes,
    list_env_files,
    load_apps,
    merge_env_keys,
    migrate_legacy_env,
    parse_env_file,
    rebuild_app,
    run_cmd,
)
```

- [ ] **Step 2: Replace `edit_env_file`**

Replace the entire function body (lines 323–405) with:

```python
def edit_env_file(
    app_cfg: "AppConfig", filename: str = ".env",
) -> tuple[bool, str | None]:
    """Open one of the app's managed env files in $EDITOR.

    Creates the file 0600 if missing, with a header that depends on whether
    the repo has a same-named file we could seed from. Returns (changed, error)
    where `changed` is True iff mtime advanced.
    """
    if (
        not filename.startswith(".env")
        or "/" in filename
        or filename in (".", "..")
    ):
        return (False, f"invalid env filename: {filename!r}")
    try:
        ENV_DIR.mkdir(parents=True, exist_ok=True)
        os.chmod(ENV_DIR, 0o700)
    except OSError as e:
        return (False, f"cannot create {ENV_DIR}: {e}")

    migrate_legacy_env(app_cfg.name)
    app_dir = env_dir_for(app_cfg.name)
    try:
        app_dir.mkdir(parents=True, exist_ok=True)
        os.chmod(app_dir, 0o700)
    except OSError as e:
        return (False, f"cannot create {app_dir}: {e}")

    path = app_dir / filename
    if not path.exists():
        # Seed from a same-named repo file if present and not git-tracked.
        repo_src = app_cfg.repo_path / filename
        seed_bytes: bytes | None = None
        if repo_src.is_file():
            try:
                seed_bytes = repo_src.read_bytes()
            except OSError as e:
                return (False, f"cannot read {repo_src}: {e}")
            header = (
                f"# ServerTUI: imported from {repo_src} on first edit.\n"
                f"# Canonical: {path} (0600).\n"
                f"# In compose mode, ServerTUI symlinks this back into the repo.\n"
                f"# Safe to delete these header lines.\n\n"
            ).encode()
            content = header + seed_bytes
        else:
            content = (
                f"# ServerTUI env file for app '{app_cfg.name}': {filename}\n"
                f"# Canonical: {path} (0600).\n"
                f"# In compose mode, ServerTUI symlinks this back into the repo\n"
                f"# as {app_cfg.repo_path / filename} at rebuild time.\n"
                f"# Add KEY=value lines below.\n"
            ).encode()
        try:
            fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            try:
                os.write(fd, content)
            finally:
                os.close(fd)
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

    try:
        st_after = path.stat()
    except OSError as e:
        return (False, f"cannot stat {path} after edit: {e}")
    if (st_after.st_mode & 0o777) != 0o600:
        try:
            os.chmod(path, 0o600)
        except OSError as e:
            return (False, f"{path} perms drifted to "
                           f"{oct(st_after.st_mode & 0o777)} and chmod failed: {e}")
    return (st_after.st_mtime > before, None)
```

- [ ] **Step 3: Static check**

```bash
.venv/bin/python -c "import ast; ast.parse(open('src/servertui/tui.py').read()); print('syntax ok')"
```

Expected: `syntax ok`.

- [ ] **Step 4: Commit**

```bash
git add src/servertui/tui.py
git commit -m "Adapt edit_env_file to per-app env directory with filename argument"
```

---

## Task 6: Refactor `action_app_edit_env` to add file-picker step

**Files:**
- Modify: `src/servertui/tui.py` — `action_app_edit_env` (line 1284).

- [ ] **Step 1: Replace the action body**

Replace lines 1284–1335 (the entire `action_app_edit_env` method) with:

```python
    def action_app_edit_env(self) -> None:
        items = self._app_items()
        if not items:
            self.notify("No apps configured", severity="warning")
            return

        ENV_PRESETS = (".env", ".env.local", ".env.production",
                       ".env.development", ".env.staging")

        def edit_and_prompt(name: str, app_cfg: "AppConfig", filename: str) -> None:
            with self.suspend():
                changed, err = edit_env_file(app_cfg, filename)

            bg_fetch_cheap()
            self._render_ui()

            if err:
                self.notify(err, severity="error")
                return
            if not changed:
                self.notify(f"{name}/{filename}: unchanged")
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
                    f"{name}/{filename} updated — restart container?",
                    [("yes", "✅ yes, restart now"), ("no", "❌ no")],
                ),
                maybe_restart,
            )

        def on_app(name: str | None) -> None:
            if name is None:
                return
            app_cfg = next((a for a in APPS if a.name == name), None)
            if app_cfg is None:
                return

            migrate_legacy_env(name)
            existing = [p.name for p in list_env_files(name)]
            file_items: list[tuple[str, str]] = [
                (n, f"{n}  [dim](edit)[/]") for n in existing
            ]
            for preset in ENV_PRESETS:
                if preset not in existing:
                    file_items.append((preset, f"{preset}  [dim](new)[/]"))

            def on_file(filename: str | None) -> None:
                if filename is None:
                    return
                edit_and_prompt(name, app_cfg, filename)

            self.push_screen(
                SelectorScreen(f"{name}: pick env file", file_items),
                on_file,
            )

        self.push_screen(SelectorScreen("Edit App Env", items), on_app)
```

- [ ] **Step 2: Static check**

```bash
.venv/bin/python -c "import ast; ast.parse(open('src/servertui/tui.py').read()); print('syntax ok')"
```

Expected: `syntax ok`.

- [ ] **Step 3: Commit**

```bash
git add src/servertui/tui.py
git commit -m "Add env file picker between app selector and editor"
```

---

## Task 7: Retarget `action_app_import_env` to per-source destinations

**Files:**
- Modify: `src/servertui/tui.py` — `action_app_import_env` (line 1337).

- [ ] **Step 1: Replace the canonical-path computation**

Find (around line 1381):

```python
                canonical = ENV_DIR / f"{app_cfg.name}.env"
                canonical_keys: set[str] = set()
                if canonical.exists():
```

Replace with:

```python
                migrate_legacy_env(app_cfg.name)
                dest_dir = env_dir_for(app_cfg.name)
                try:
                    dest_dir.mkdir(parents=True, exist_ok=True)
                    os.chmod(dest_dir, 0o700)
                except OSError as e:
                    self.notify(f"cannot create {dest_dir}: {e}",
                                severity="error")
                    return
                canonical = dest_dir / source_name
                canonical_keys: set[str] = set()
                if canonical.exists():
```

The rest of the function (perms check on `canonical`, `parse_env_file(canonical)` for existing keys, the `merge_env_keys(canonical, selected, source_name)` call, the restart prompt) is unchanged because they all operate on the local `canonical` variable.

- [ ] **Step 2: Static check**

```bash
.venv/bin/python -c "import ast; ast.parse(open('src/servertui/tui.py').read()); print('syntax ok')"
```

Expected: `syntax ok`.

- [ ] **Step 3: Commit**

```bash
git add src/servertui/tui.py
git commit -m "Retarget I=import to per-source-named files in app env dir"
```

---

## Task 8: Docs + version bump

**Files:**
- Modify: `README.md` — env-files section.
- Modify: `CLAUDE.md` — verify keybindings line still accurate (`R/E/I/L`); update env section if present.
- Modify: `pyproject.toml` — bump version to `0.3.0`.
- Modify: `src/servertui/__init__.py` — bump `__version__` to `"0.3.0"` if defined.

- [ ] **Step 1: Update `README.md` env section**

Replace the existing "Env files" subsection with:

```markdown
### Env files

Each app gets a directory at `~/.config/servertui/env/<name>/` for its
dotenv files (`.env`, `.env.production`, `.env.local`, etc.). ServerTUI
creates files with `0600` permissions and refuses to deploy if any drift.

- **Dockerfile mode:** each managed file is passed to `docker run` as a
  separate `--env-file` (later files override earlier ones — sort order is
  filename-ascending).
- **Compose mode:** each managed file is symlinked into the repo as
  `<repo>/<filename>` before `docker compose up`, so `env_file:` entries
  and `${VAR}` interpolation just work. ServerTUI refuses to symlink over
  a git-tracked file.

**Editing:** press `E`, pick the app, then pick which file to edit (existing
files plus the common presets `.env / .env.local / .env.production /
.env.development / .env.staging`).

**Importing:** press `I` to copy keys from a `<repo>/.env*` source into the
matching managed file (e.g. `<repo>/.env.production` → `~/.config/servertui/env/<name>/.env.production`).

Legacy `~/.config/servertui/env/<name>.env` files are migrated automatically
to `<name>/.env` on first read.
```

- [ ] **Step 2: Bump version**

```bash
sed -i 's/^version = "0\.2\.1"$/version = "0.3.0"/' pyproject.toml
grep -n "0.3.0" pyproject.toml  # confirm
```

If `src/servertui/__init__.py` defines `__version__`, update it too.

- [ ] **Step 3: Verify CLAUDE.md keybindings**

The `R/E/I/L` cluster is unchanged; only behavior shifts. If the env paragraph in `CLAUDE.md` mentions a single `<name>.env` file, refresh it to match the new directory model (one or two sentences).

- [ ] **Step 4: Commit**

```bash
git add README.md CLAUDE.md pyproject.toml src/servertui/__init__.py
git commit -m "Bump to 0.3.0 and document directory-per-app env model"
```

---

## Task 9: Final smoke matrix on the dev machine

Run on the user's actual server (where the deployed apps live), not in CI.

- [ ] **Migration check**

```bash
ls -la ~/.config/servertui/env/
.venv/bin/python -c "import sys; sys.path.insert(0, 'src'); from servertui.core import fetch_app_status, load_apps; fetch_app_status(load_apps(), {})"
ls -la ~/.config/servertui/env/
ls -la ~/.config/servertui/env/coloring/ 2>/dev/null
```

Every legacy `<name>.env` should have moved to `<name>/.env` with perms preserved.

- [ ] **Compose, single file**

For an app with one managed `.env`: trigger rebuild via the MCP server.

```bash
# (From a separate Claude Code session or via the `servertui` CLI directly.)
ls -la ~/servertui/apps/<app>/.env
docker inspect servertui-<app> | grep -A3 Env | head -20
```

The repo `.env` should be a symlink pointing into `~/.config/servertui/env/<app>/.env`. The container env should reflect the file contents.

- [ ] **Compose, multi-file (the original coloring-cafe scenario)**

Add a `.env.production` to `~/.config/servertui/env/coloring/` (copy from your existing source, `chmod 600`). Trigger rebuild.

```bash
ls -la ~/servertui/apps/coloring/.env ~/servertui/apps/coloring/.env.production
```

Both should be symlinks. `docker compose up` should succeed without the previous "not auto-wired" warning.

- [ ] **Tracked-file collision (negative test)**

In a scratch repo, commit a `.env` file. Run rebuild. Output must include `is tracked in git -- refusing to overwrite` and exit 1.

- [ ] **Dockerfile mode, multi-file**

For an app on dockerfile mode with two managed env files: `docker inspect <container>` and check the run command included two `--env-file` flags.

- [ ] **`E` flow, all branches**

- App with no managed files → picker shows only presets; pick `.env`, save, file appears at `<name>/.env` with seeded header (or repo-seeded header if `<repo>/.env` existed).
- App with one managed file → picker shows the file (edit) plus four `[new]` presets.
- Pick a `[new]` preset that has a same-named file in the repo → opened file contains the seed header + repo contents.
- Pick a `[new]` preset that has no repo counterpart → opened file contains the empty-template header only.

- [ ] **`I` flow**

- Press `I`, pick app, pick `.env.production` from repo source list. Verify destination is `~/.config/servertui/env/<name>/.env.production` (not `<name>.env`). Verify "replaces" markers reflect that file's existing keys, not the legacy single canonical.

- [ ] **Idempotent rebuild**

Run the same rebuild twice in a row. The second run should print `env wired (unchanged): <file>` lines, no relink, no error.

- [ ] **Status panel aggregation**

In the TUI, observe `env: N keys` for an app with two managed files; N must be the sum across both. Drop the perms on one file (`chmod 644`) and confirm the perms warning fires.

---

## Task 10: Open the PR

- [ ] **Step 1: Push and open**

```bash
git push -u origin multi-env-files
gh pr create --title "Per-app env directory + compose-mode auto-wire (v0.3.0)" \
  --body "$(cat <<'EOF'
## Summary
- Replaces the single `~/.config/servertui/env/<name>.env` with a per-app directory `~/.config/servertui/env/<name>/` that holds arbitrary `.env*` files.
- Compose mode now symlinks each managed file into `<repo>/<filename>` before `docker compose up`, so `env_file:` entries and `${VAR}` interpolation just work. Refuses to symlink over a git-tracked file.
- Dockerfile mode passes each managed file to `docker run` as a separate `--env-file`.
- `E` (edit env) gains a file-picker step between the app selector and the editor — existing files plus presets `.env / .env.local / .env.production / .env.development / .env.staging`.
- `I` (import env) retargets: importing from `<repo>/.env.production` lands in `~/.config/servertui/env/<name>/.env.production`, not the old single canonical.
- Legacy `<name>.env` files migrate automatically to `<name>/.env` on first read or first edit.
- Version bumped to 0.3.0.

## Spec & plan
- `docs/superpowers/specs/2026-05-04-multi-env-files-design.md`
- `docs/superpowers/plans/2026-05-04-multi-env-files.md`

## Test plan
- [ ] Migration: legacy `<name>.env` → `<name>/.env` after first status fetch, perms preserved.
- [ ] Compose, single managed file: `<repo>/.env` is a symlink, container picks up env.
- [ ] Compose, multi-file (the coloring-cafe BE+FE case): both `.env` and `.env.production` symlinked, `docker compose up` succeeds.
- [ ] Tracked-file collision aborts the rebuild with the documented error.
- [ ] Dockerfile mode with two managed env files passes both via `--env-file`.
- [ ] `E` picker works for empty/single/multi-file apps; preset that matches a repo file seeds from the repo with header.
- [ ] `I` import lands in the matching destination filename, not the legacy canonical.
- [ ] Idempotent rebuild produces no symlink churn.
- [ ] Status panel aggregates key count across files; perms warning fires per-file.
EOF
)"
```

- [ ] **Step 2: Note in PR for the maintainer**

After opening, comment on the PR confirming the deploy story: the running MCP/TUI is installed via `uv tool install servertui` from PyPI, so picking up the change requires republishing (`0.3.0` tag → release workflow at `.github/workflows/release.yml`) and `uv tool upgrade servertui`. Mention this so the maintainer can plan the release cut and the staged smoke run on the live host.
