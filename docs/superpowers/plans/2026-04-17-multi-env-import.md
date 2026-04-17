# Multi-`.env` Import Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add an explicit "Import env" action (`I`) on the Apps tab that lets the user pick a source `.env*` file from the app's repo and import selected keys into the canonical `~/.config/servertui/env/<name>.env`.

**Architecture:** Pure parse/merge helpers in `core.py`; a new `ImportKeysScreen` modal and an `action_app_import_env` orchestrator in `app.py`. Builds on PR #2's first-edit seeding; only change to `E` flow is the multi-candidate case now writes a template with a breadcrumb instead of silently picking `.env`.

**Tech Stack:** Python 3.11+, Textual (SelectionList for per-key picker, ModalScreen for the modal). No new dependencies.

**Branch:** Continues on `debug/env-read-write` (same branch as PR #2). Merge PR #2 first if possible; otherwise these commits stack on top.

**Spec:** `docs/superpowers/specs/2026-04-17-multi-env-import-design.md`

---

## File Structure

- **Modify `core.py`** — add `parse_env_file` and `merge_env_keys` after `inspect_env_file` (near line 193).
- **Modify `app.py`**:
  - Adjust `edit_env_file` (line 319) to branch on single-vs-multi candidates.
  - Add `ImportKeysScreen` modal class near `SelectorScreen` (after line 726).
  - Extend `app_items` footer hint (line 649).
  - Register `I` binding (line 912 cluster).
  - Add `action_app_import_env` method near `action_app_edit_env` (line 1163).
  - Update import from `core` to include the two new helpers.
- **Modify `CLAUDE.md`** — keybindings line at the bottom.

---

## Task 1: `parse_env_file` in `core.py`

**Files:**
- Modify: `core.py` — insert after `inspect_env_file` (around line 193)

- [ ] **Step 1: Add the function to `core.py`**

Insert immediately after the closing `return` of `inspect_env_file` (line ~192), before `def detect_build_mode`:

```python
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
            # Find closing quote (respecting escapes for ")
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
                inner = (inner.replace(r"\n", "\n")
                              .replace(r"\t", "\t")
                              .replace(r'\"', '"')
                              .replace(r"\\", "\\"))
            value = inner
        else:
            # Strip inline comment: # starts a comment when preceded by whitespace
            hash_idx = -1
            for i, ch in enumerate(value):
                if ch == "#" and (i == 0 or value[i - 1].isspace()):
                    hash_idx = i
                    break
            if hash_idx != -1:
                value = value[:hash_idx]
            value = value.rstrip()
        if key in pairs:
            # Preserve original order position; update value.
            pairs[key] = value
        else:
            pairs[key] = value
            order.append(key)
    return [(k, pairs[k]) for k in order]
```

Also ensure `import re` and `import sys` are present at the top of `core.py` (check existing imports first; likely already there — if not, add them).

- [ ] **Step 2: Write smoke test and run it**

From the worktree root, run this heredoc script. It shims `docker`/`psutil` so `core.py` imports without venv.

```bash
python3 <<'PY'
import sys, types, tempfile
from pathlib import Path
for mod in ("docker", "docker.errors", "psutil"):
    sys.modules.setdefault(mod, types.ModuleType(mod))
sys.modules["docker.errors"].NotFound = type("NotFound", (Exception,), {})
sys.modules["docker"].errors = sys.modules["docker.errors"]
sys.modules["docker"].from_env = lambda: None
sys.path.insert(0, ".")
from core import parse_env_file

tmp = Path(tempfile.mkdtemp())
p = tmp / "t.env"

# Case 1: bare, quoted, export, comment, dup
p.write_text(
    "# top comment\n"
    "\n"
    "FOO=bar\n"
    "export BAZ=qux\n"
    'QUOTED="hello world"\n'
    "SINGLE='raw$val'\n"
    "ESC=\"line1\\nline2\"\n"
    "WITH_HASH=value # trailing\n"
    "FOO=overridden\n"
    "BAD line without equals\n"
    "123BAD=nope\n"
    "EMPTY=\n"
)
got = parse_env_file(p)
expected = [
    ("FOO", "overridden"),
    ("BAZ", "qux"),
    ("QUOTED", "hello world"),
    ("SINGLE", "raw$val"),
    ("ESC", "line1\nline2"),
    ("WITH_HASH", "value"),
    ("EMPTY", ""),
]
assert got == expected, f"got {got}\nexpected {expected}"
print("CASE1 OK")

# Case 2: missing file
assert parse_env_file(tmp / "nope.env") == []
print("CASE2 OK")

# Case 3: empty file
(tmp / "e.env").write_text("")
assert parse_env_file(tmp / "e.env") == []
print("CASE3 OK")

print("\nALL PASS")
PY
```

Expected: prints `CASE1 OK`, `CASE2 OK`, `CASE3 OK`, `ALL PASS`. Stderr may show messages about skipped malformed lines — that's expected.

- [ ] **Step 3: Commit**

```bash
git add core.py
git commit -m "Add parse_env_file helper to core"
```

---

## Task 2: `merge_env_keys` in `core.py`

**Files:**
- Modify: `core.py` — insert after `parse_env_file`

- [ ] **Step 1: Add the function to `core.py`**

Immediately after `parse_env_file`:

```python
def _quote_env_value(value: str) -> str:
    """Emit a .env-compatible serialization of value."""
    if value == "":
        return ""
    safe = set("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ"
               "0123456789_./:@+-")
    if all(ch in safe for ch in value):
        return value
    escaped = value.replace("\\", "\\\\").replace('"', '\\"')
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
    import datetime
    if not updates:
        return 0

    if not canonical_path.exists():
        header = (
            f"# ServerTUI env file for '{canonical_path.stem}'\n"
            f"# Location: {canonical_path} (0600, injected via docker --env-file).\n"
            f"# Stored outside the git repo so secrets stay uncommitted.\n"
        ).encode()
        fd = os.open(canonical_path,
                     os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        try:
            os.write(fd, header)
        finally:
            os.close(fd)

    text = canonical_path.read_text(encoding="utf-8", errors="replace")
    lines = text.splitlines(keepends=True)

    # Build key -> list of line indices (in file order).
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

    # Rewrite in_place; mark earlier duplicates for deletion.
    to_delete: set[int] = set()
    for key, value in in_place:
        indices = key_lines[key]
        last = indices[-1]
        lines[last] = f"{key}={_quote_env_value(value)}\n"
        for earlier in indices[:-1]:
            to_delete.add(earlier)
    if to_delete:
        lines = [line for i, line in enumerate(lines) if i not in to_delete]

    # Append new keys with a marker.
    if appended:
        if lines and not lines[-1].endswith("\n"):
            lines[-1] = lines[-1] + "\n"
        today = datetime.date.today().isoformat()
        lines.append("\n")
        lines.append(f"# imported {today} from {source_label}\n")
        for key, value in appended:
            lines.append(f"{key}={_quote_env_value(value)}\n")

    # Atomic write.
    tmp_path = canonical_path.with_suffix(canonical_path.suffix + ".tmp")
    # Remove stale tmp if present (e.g. crashed prior run).
    if tmp_path.exists():
        tmp_path.unlink()
    fd = os.open(tmp_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        os.write(fd, "".join(lines).encode("utf-8"))
    finally:
        os.close(fd)
    os.replace(tmp_path, canonical_path)
    return len(updates)
```

Make sure `import os` and `import re` are already at the top of `core.py` (both should be — check).

- [ ] **Step 2: Smoke test `merge_env_keys`**

```bash
python3 <<'PY'
import sys, types, tempfile, os, stat
from pathlib import Path
for mod in ("docker", "docker.errors", "psutil"):
    sys.modules.setdefault(mod, types.ModuleType(mod))
sys.modules["docker.errors"].NotFound = type("NotFound", (Exception,), {})
sys.modules["docker"].errors = sys.modules["docker.errors"]
sys.modules["docker"].from_env = lambda: None
sys.path.insert(0, ".")
from core import merge_env_keys, parse_env_file, _quote_env_value

tmp = Path(tempfile.mkdtemp())

# Case 1: creates file when absent
p1 = tmp / "app1.env"
n = merge_env_keys(p1, [("FOO", "bar"), ("BAZ", "q x")], ".env.local")
assert n == 2
c1 = p1.read_text()
assert "FOO=bar" in c1 and 'BAZ="q x"' in c1
assert "# imported" in c1 and "from .env.local" in c1
m = p1.stat().st_mode & 0o777
assert m == 0o600, f"perms={oct(m)}"
print("CASE1 OK:", c1.count("\n"), "lines")

# Case 2: in-place replace preserves comments
p2 = tmp / "app2.env"
p2.write_text(
    "# my comment\n"
    "FOO=old\n"
    "\n"
    "KEEP=me\n"
    "FOO=older_dup\n"
)
p2.chmod(0o600)
n = merge_env_keys(p2, [("FOO", "new"), ("NEW", "val")], ".env")
c2 = p2.read_text()
assert "# my comment" in c2
assert "KEEP=me" in c2
# Dedup: only one FOO line now
assert c2.count("FOO=") == 1
assert "FOO=new" in c2
assert "NEW=val" in c2
# Appended under marker
assert "# imported" in c2
print("CASE2 OK\n" + c2 + "---")

# Case 3: empty updates is a no-op
p3 = tmp / "app3.env"
p3.write_text("FOO=bar\n")
p3.chmod(0o600)
n = merge_env_keys(p3, [], ".env")
assert n == 0
assert p3.read_text() == "FOO=bar\n"
print("CASE3 OK")

# Case 4: quoted value with special chars round-trips via parse
p4 = tmp / "app4.env"
merge_env_keys(p4, [("X", 'a"b c')], ".env.staging")
got = dict(parse_env_file(p4))
assert got["X"] == 'a"b c', f"got {got['X']!r}"
print("CASE4 OK")

# Case 5: stale tmp file from a prior crash is cleaned up
p5 = tmp / "app5.env"
p5.write_text("FOO=old\n"); p5.chmod(0o600)
stale = p5.with_suffix(p5.suffix + ".tmp")
stale.write_text("garbage")
merge_env_keys(p5, [("FOO", "new")], ".env")
assert not stale.exists()
assert "FOO=new" in p5.read_text()
print("CASE5 OK")

print("\nALL PASS")
PY
```

Expected: all five cases print OK, then `ALL PASS`.

- [ ] **Step 3: Commit**

```bash
git add core.py
git commit -m "Add merge_env_keys helper with atomic write and dedup"
```

---

## Task 3: Adjust `edit_env_file` for multi-candidate case

**Files:**
- Modify: `app.py:319-362` (the `edit_env_file` function)

- [ ] **Step 1: Replace the file-creation block in `edit_env_file`**

Use the Edit tool to replace the current `if not path.exists():` block (the one committed in PR #2, lines 329-357). The only change: when multiple candidates exist, skip auto-import and write a template with a breadcrumb.

Find the block that currently reads:

```python
    if not path.exists():
        repo_env = app_cfg.repo_path / ".env"
        if repo_env.is_file():
            try:
                repo_bytes = repo_env.read_bytes()
            except OSError as e:
                return (False, f"cannot read {repo_env}: {e}")
            header = (
                f"# ServerTUI: imported from {repo_env} on first edit.\n"
                f"# Canonical location: {path} (0600, injected via "
                f"docker --env-file).\n"
                f"# Safe to delete these header lines.\n\n"
            ).encode()
            content = header + repo_bytes
        else:
            content = (
                f"# ServerTUI env file for app '{app_cfg.name}'\n"
                f"# Location: {path} (0600, injected via docker --env-file "
                f"at rebuild).\n"
                f"# Stored outside the git repo so secrets stay uncommitted.\n"
                f"# Add KEY=value lines below.\n"
            ).encode()
```

Replace with:

```python
    if not path.exists():
        candidate_names = (".env", ".env.local", ".env.development",
                           ".env.staging", ".env.production")
        candidates = [app_cfg.repo_path / n for n in candidate_names
                      if (app_cfg.repo_path / n).is_file()]
        if len(candidates) == 1:
            src = candidates[0]
            try:
                repo_bytes = src.read_bytes()
            except OSError as e:
                return (False, f"cannot read {src}: {e}")
            header = (
                f"# ServerTUI: imported from {src} on first edit.\n"
                f"# Canonical location: {path} (0600, injected via "
                f"docker --env-file).\n"
                f"# Safe to delete these header lines.\n\n"
            ).encode()
            content = header + repo_bytes
        elif len(candidates) > 1:
            names = ", ".join(c.name for c in candidates)
            content = (
                f"# ServerTUI env file for app '{app_cfg.name}'\n"
                f"# Location: {path} (0600, injected via docker --env-file "
                f"at rebuild).\n"
                f"# Multiple .env* files detected in repo: {names}\n"
                f"# Press I on the Apps tab to import from a specific source.\n"
                f"# Add KEY=value lines below.\n"
            ).encode()
        else:
            content = (
                f"# ServerTUI env file for app '{app_cfg.name}'\n"
                f"# Location: {path} (0600, injected via docker --env-file "
                f"at rebuild).\n"
                f"# Stored outside the git repo so secrets stay uncommitted.\n"
                f"# Add KEY=value lines below.\n"
            ).encode()
```

Leave the `os.open(..., O_EXCL, 0o600)` + write block that follows unchanged.

- [ ] **Step 2: Syntax check**

```bash
python3 -c "import ast; ast.parse(open('app.py').read()); print('OK')"
```

Expected: `OK`.

- [ ] **Step 3: Smoke test the three branches**

```bash
python3 <<'PY'
import sys, types, os, tempfile, shutil
from pathlib import Path

class _G:
    def __class_getitem__(cls, item): return cls
    def __init__(self, *a, **k): pass

for mod in ("docker","docker.errors","psutil","textual","textual.app",
            "textual.binding","textual.containers","textual.screen",
            "textual.widgets","textual.timer"):
    sys.modules.setdefault(mod, types.ModuleType(mod))
sys.modules["docker"].from_env = lambda: None
sys.modules["docker.errors"].NotFound = type("NotFound", (Exception,), {})
sys.modules["docker"].errors = sys.modules["docker.errors"]
sys.modules["textual.app"].App = _G
sys.modules["textual.app"].ComposeResult = object
sys.modules["textual.binding"].Binding = lambda *a, **k: None
for n in ("Container","Horizontal","Vertical"):
    setattr(sys.modules["textual.containers"], n, type(n,(_G,),{}))
sys.modules["textual.screen"].ModalScreen = _G
for n in ("DataTable","Footer","Header","Static","TabbedContent","TabPane",
          "Button","Label","ListView","ListItem","Log","RichLog","Input",
          "SelectionList"):
    setattr(sys.modules["textual.widgets"], n, type(n,(_G,),{}))
sys.modules["textual.timer"].Timer = _G

tmp = Path(tempfile.mkdtemp())
try:
    os.environ["SERVERTUI_APPS_DIR"] = str(tmp / "apps")
    os.environ["EDITOR"] = "true"
    sys.path.insert(0, ".")
    import core, app
    core.ENV_DIR = tmp / "env"; app.ENV_DIR = tmp / "env"
    AppCfg = core.App

    # One candidate -> auto-import (PR #2 path, unchanged)
    (tmp/"apps/alpha").mkdir(parents=True)
    (tmp/"apps/alpha/.env").write_text("FOO=bar\n")
    _, err = app.edit_env_file(AppCfg(name="alpha", git_url="x"))
    txt = (tmp/"env/alpha.env").read_text()
    assert err is None and "FOO=bar" in txt and "imported from" in txt
    print("SINGLE OK")

    # Multiple candidates -> template with breadcrumb, no auto-import
    (tmp/"apps/beta").mkdir(parents=True)
    (tmp/"apps/beta/.env").write_text("A=1\n")
    (tmp/"apps/beta/.env.staging").write_text("A=staging\n")
    _, err = app.edit_env_file(AppCfg(name="beta", git_url="x"))
    txt = (tmp/"env/beta.env").read_text()
    assert err is None
    assert "Multiple .env* files detected" in txt
    assert ".env, .env.staging" in txt
    assert "A=1" not in txt and "A=staging" not in txt
    print("MULTI OK")

    # Zero candidates -> plain template
    (tmp/"apps/gamma").mkdir(parents=True)
    _, err = app.edit_env_file(AppCfg(name="gamma", git_url="x"))
    txt = (tmp/"env/gamma.env").read_text()
    assert err is None and "Multiple" not in txt and "imported" not in txt
    assert "Add KEY=value" in txt
    print("ZERO OK")

    print("\nALL PASS")
finally:
    shutil.rmtree(tmp)
PY
```

Expected: `SINGLE OK`, `MULTI OK`, `ZERO OK`, `ALL PASS`.

- [ ] **Step 4: Commit**

```bash
git add app.py
git commit -m "Skip auto-seed in edit_env_file when multiple .env* candidates exist"
```

---

## Task 4: `ImportKeysScreen` modal

**Files:**
- Modify: `app.py` — insert new class after `SelectorScreen` (line 726), before `class LogScreen`

- [ ] **Step 1: Add the import for `SelectionList` to `app.py`**

Find the `from textual.widgets import (...)` block near the top (around line 38). Add `SelectionList` to the list. If it's not in `textual.widgets`, check `from textual.widgets import SelectionList` (version-dependent). Verify with:

```bash
python3 -c "from textual.widgets import SelectionList; print('OK')"
```

If it errors, try `from textual.widgets._selection_list import SelectionList` and adjust the import accordingly.

(If neither path works because of the installed Textual version, use `ListView` + `ListItem` with manual toggle state instead; document this fallback in the commit. SelectionList has been in Textual since 0.27 which is well below our requirements.)

- [ ] **Step 2: Add the modal class**

Insert after the closing of `SelectorScreen` (line 727), before `class LogScreen`:

```python
class ImportKeysScreen(ModalScreen[list[tuple[str, str]] | None]):
    """Per-key selection modal for env import. Returns selected pairs or None."""

    BINDINGS = [
        Binding("escape", "cancel", "Cancel"),
        Binding("enter", "submit", "Import"),
    ]

    def __init__(
        self,
        source_name: str,
        pairs: list[tuple[str, str]],
        canonical_keys: set[str],
    ) -> None:
        super().__init__()
        self.source_name = source_name
        self.pairs = pairs
        self.canonical_keys = canonical_keys

    def compose(self) -> ComposeResult:
        with Container(id="selector-box"):
            yield Static(
                f"[bold]Import from {self.source_name}[/]\n"
                f"[dim]Space to toggle, Enter to import, Esc to cancel[/]\n",
                id="selector-title",
            )
            options = []
            for i, (key, value) in enumerate(self.pairs):
                preview = value if len(value) <= 40 else value[:37] + "…"
                tag = "[yellow]replaces[/]" if key in self.canonical_keys else "[green]new[/]"
                label = f"{key}={preview}  {tag}"
                options.append((label, i, True))  # all selected by default
            yield SelectionList[int](*options, id="import-list")

    def action_submit(self) -> None:
        sl = self.query_one("#import-list", SelectionList)
        indices = sorted(sl.selected)
        result = [self.pairs[i] for i in indices]
        self.dismiss(result)

    def action_cancel(self) -> None:
        self.dismiss(None)
```

- [ ] **Step 3: Syntax check**

```bash
python3 -c "import ast; ast.parse(open('app.py').read()); print('OK')"
```

Expected: `OK`.

- [ ] **Step 4: Commit**

```bash
git add app.py
git commit -m "Add ImportKeysScreen modal for per-key env import"
```

---

## Task 5: `action_app_import_env` + binding + footer

**Files:**
- Modify: `app.py` — imports (line 22-34), footer hint (line 649), bindings (line 912), add action after `action_app_edit_env` (line 1212)

- [ ] **Step 1: Extend the `core` import**

In `app.py:22-34`, add `parse_env_file` and `merge_env_keys` to the `from core import (...)` block. Sorted alphabetically, it becomes:

```python
from core import (
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

- [ ] **Step 2: Update the Apps footer hint (line 649)**

Use the Edit tool:

Old:
```python
            "[dim]  ⌨  [bold]R[/]=rebuild  [bold]E[/]=edit env  [bold]L[/]=logs[/]"
```

New:
```python
            "[dim]  ⌨  [bold]R[/]=rebuild  [bold]E[/]=edit env  "
            "[bold]I[/]=import env  [bold]L[/]=logs[/]"
```

- [ ] **Step 3: Register the `I` binding**

Near line 912, add after `Binding("E", "app_edit_env", "Edit env"),`:

```python
        Binding("I", "app_import_env", "Import env"),
```

- [ ] **Step 4: Add `action_app_import_env`**

Insert immediately after the closing of `action_app_edit_env` (after line 1214), before `action_show_tab`:

```python
    def action_app_import_env(self) -> None:
        items = self._app_items()
        if not items:
            self.notify("No apps configured", severity="warning")
            return

        candidate_names = (".env", ".env.local", ".env.development",
                           ".env.staging", ".env.production")

        def on_app_chosen(name: str | None) -> None:
            if name is None:
                return
            app_cfg = next((a for a in APPS if a.name == name), None)
            if app_cfg is None:
                return
            if not app_cfg.repo_path.is_dir():
                self.notify(
                    f"{name}: repo not cloned — run Rebuild first",
                    severity="warning",
                )
                return
            candidates = [
                app_cfg.repo_path / n
                for n in candidate_names
                if (app_cfg.repo_path / n).is_file()
            ]
            if not candidates:
                self.notify(
                    f"{name}: no .env files found in {app_cfg.repo_path}",
                    severity="warning",
                )
                return

            def on_source_chosen(source_name: str | None) -> None:
                if source_name is None:
                    return
                source_path = app_cfg.repo_path / source_name
                pairs = parse_env_file(source_path)
                if not pairs:
                    self.notify(
                        f"no keys to import from {source_name}",
                        severity="warning",
                    )
                    return
                canonical = ENV_DIR / f"{app_cfg.name}.env"
                canonical_keys: set[str] = set()
                if canonical.exists():
                    st = canonical.stat()
                    if (st.st_mode & 0o777) != 0o600:
                        self.notify(
                            f"refusing to write: {canonical} has perms "
                            f"{oct(st.st_mode & 0o777)}, expected 0o600",
                            severity="error",
                        )
                        return
                    canonical_keys = {k for k, _ in parse_env_file(canonical)}

                def on_keys_chosen(
                    selected: list[tuple[str, str]] | None,
                ) -> None:
                    if selected is None:
                        return
                    if not selected:
                        self.notify("import cancelled — no keys selected")
                        return
                    try:
                        count = merge_env_keys(canonical, selected, source_name)
                    except OSError as e:
                        self.notify(f"merge failed: {e}", severity="error")
                        return
                    bg_fetch_cheap()
                    self._render_ui()
                    self.notify(
                        f"{name}: imported {count} keys from {source_name}"
                    )

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
                            self.notify(
                                f"Restart failed: {e}", severity="error"
                            )
                        self._start_bg_fetch()

                    self.push_screen(
                        SelectorScreen(
                            f"{name}: env updated — restart container?",
                            [("yes", "✅ yes, restart now"),
                             ("no", "❌ no")],
                        ),
                        maybe_restart,
                    )

                self.push_screen(
                    ImportKeysScreen(source_name, pairs, canonical_keys),
                    on_keys_chosen,
                )

            source_items = [(c.name, c.name) for c in candidates]
            self.push_screen(
                SelectorScreen(f"{name}: pick source .env file", source_items),
                on_source_chosen,
            )

        self.push_screen(SelectorScreen("Import App Env", items), on_app_chosen)
```

- [ ] **Step 5: Syntax check**

```bash
python3 -c "import ast; ast.parse(open('app.py').read()); print('OK')"
```

Expected: `OK`.

- [ ] **Step 6: Commit**

```bash
git add app.py
git commit -m "Add I=import env action with source picker and per-key selection"
```

---

## Task 6: Docs + final verification

**Files:**
- Modify: `CLAUDE.md` — keybindings line

- [ ] **Step 1: Update `CLAUDE.md`**

Find the Keybindings section at the bottom of `CLAUDE.md`. The current line reads:

```
`1-5` switch tab · `s/t/r/l` tunnel start/stop/restart/logs · `u/d/x` container start/stop/restart · `g` timer logs · `R/E/L` app rebuild/edit-env/logs · `f` force refresh · `q` quit
```

Replace `R/E/L` cluster with `R/E/I/L app rebuild/edit-env/import-env/logs`:

```
`1-5` switch tab · `s/t/r/l` tunnel start/stop/restart/logs · `u/d/x` container start/stop/restart · `g` timer logs · `R/E/I/L` app rebuild/edit-env/import-env/logs · `f` force refresh · `q` quit
```

- [ ] **Step 2: Commit docs**

```bash
git add CLAUDE.md
git commit -m "Document I=import env keybinding"
```

- [ ] **Step 3: Manual verification on server**

On the server (not the laptop):

```bash
cd <servertui-repo>
git fetch
git checkout debug/env-read-write
./run.sh
```

Then press `5` to switch to Apps tab. Verify:

1. **Single-candidate app** (repo has only `.env`): press `I`, pick the app, pick `.env`, see all keys checked with `[replaces]` (if canonical exists) or `[new]`. Submit with 0 toggles → `"import cancelled"`. Submit with all → keys merged; footer shows notification with count. Press restart=yes → container restarts.

2. **Multi-candidate app** (add a throwaway `.env.staging` to the repo first): press `I`, pick the app, confirm source picker lists both files. Choose `.env.staging`. Modal shows keys with collision badges matching canonical state. Toggle a subset, submit.

3. **Zero-candidate app** (repo has no `.env*`): press `I`, pick the app → notify "no .env files found".

4. **`E` regression**: delete the canonical file for a multi-candidate app, press `E` → editor opens the template with the "Multiple .env* files detected" breadcrumb line, **not** the content of any specific `.env*` file.

5. **`E` regression #2**: delete canonical for a single-`.env` app, press `E` → still auto-imports (PR #2 behavior preserved).

- [ ] **Step 4: Push and update PR**

```bash
git push origin debug/env-read-write
```

Either update PR #2's description to reflect the expanded scope, or (if PR #2 has already been merged) open a new PR from the branch. Report the PR URL back.

---

## Completion Criteria

- [ ] All six tasks above have been committed to `debug/env-read-write`.
- [ ] All three smoke test scripts (Tasks 1, 2, 3) print `ALL PASS`.
- [ ] All five manual verification scenarios in Task 6 Step 3 pass on the server.
- [ ] `CLAUDE.md` reflects the new `I` keybinding.
- [ ] PR updated (or new PR opened) with the expanded scope.
