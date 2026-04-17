# ServerTUI Installer Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Ship ServerTUI as an installable Python package on PyPI with a one-line `curl` bootstrap, a single `servertui` CLI (subcommands: tui / mcp / init), and an automated tag-driven release workflow.

**Architecture:** Restructure the flat repo (`app.py` / `core.py` / `mcp_server.py`) into a `src/servertui/` package built by hatchling. Add a stdlib-argparse CLI router and an `init` subcommand that scaffolds `~/.config/servertui/` using `apps.example.json` bundled as package data. Publish via GitHub Actions on tag push using PyPI Trusted Publishing.

**Tech Stack:** Python 3.11+, hatchling, uv, PyPI Trusted Publishing, GitHub Actions, POSIX `sh`.

**Testing approach:** No automated tests — the spec explicitly defers test/lint infrastructure. Each task includes manual runtime verification (build + run + inspect output).

**Spec:** `docs/superpowers/specs/2026-04-17-installer-design.md`

---

## File Structure

### Create

| File | Purpose |
|------|---------|
| `pyproject.toml` | Build metadata, dependencies, entry point. |
| `src/servertui/__init__.py` | Package marker + `__version__` string (single source of truth). |
| `src/servertui/__main__.py` | Enables `python -m servertui`. |
| `src/servertui/cli.py` | Argparse router for `servertui` / `servertui tui` / `servertui mcp` / `servertui init`. |
| `src/servertui/init.py` | `servertui init` — scaffolds `~/.config/servertui/`. |
| `install.sh` | Curl-able bootstrap: ensures uv, runs `uv tool install --upgrade servertui`. |
| `.github/workflows/release.yml` | Tag-driven PyPI publish via OIDC Trusted Publishing. |

### Move (via `git mv`)

| From | To |
|------|----|
| `core.py` | `src/servertui/core.py` |
| `app.py` | `src/servertui/tui.py` |
| `mcp_server.py` | `src/servertui/mcp.py` |
| `apps.example.json` | `src/servertui/apps.example.json` |

### Modify

| File | What changes |
|------|--------------|
| `src/servertui/tui.py` | Rewrite `from core import (...)` → `from servertui.core import (...)` |
| `src/servertui/mcp.py` | Same rewrite. |
| `run.sh` | Replace `.venv` activation with `uv run servertui`. |
| `README.md` | Installation section, MCP config snippet, `apps.example.json` reference. |
| `CLAUDE.md` | Run section + Architecture file paths + re-verify line numbers. |
| `AGENTS.md` | Update `mcp_server.py` path reference. |

---

## Task 1: Create package skeleton

Bootstrap the package directory + `pyproject.toml` with a stub CLI (version-only, no subcommands yet). This produces a working `uv run servertui --version` without touching the existing runtime files, so `./run.sh` still works in parallel throughout the rest of the migration.

**Files:**
- Create: `pyproject.toml`
- Create: `src/servertui/__init__.py`
- Create: `src/servertui/__main__.py`
- Create: `src/servertui/cli.py`

- [ ] **Step 1: Create `pyproject.toml`**

```toml
[build-system]
requires = ["hatchling"]
build-backend = "hatchling.build"

[project]
name = "servertui"
dynamic = ["version"]
description = "Terminal dashboard for local server infrastructure"
readme = "README.md"
requires-python = ">=3.11"
license = "MIT"
authors = [{ name = "Ilham Farobi" }]
keywords = ["tui", "textual", "docker", "cloudflare", "homelab"]
classifiers = [
    "Development Status :: 4 - Beta",
    "Environment :: Console",
    "License :: OSI Approved :: MIT License",
    "Operating System :: POSIX :: Linux",
    "Operating System :: MacOS",
    "Programming Language :: Python :: 3.11",
    "Programming Language :: Python :: 3.12",
    "Programming Language :: Python :: 3.13",
    "Topic :: System :: Systems Administration",
]
dependencies = [
    "textual>=0.60",
    "psutil>=5.9",
    "docker>=7.0",
    "mcp>=1.0",
]

[project.scripts]
servertui = "servertui.cli:main"

[project.urls]
Homepage = "https://github.com/ifarobi/servertui"
Issues  = "https://github.com/ifarobi/servertui/issues"

[tool.hatch.version]
path = "src/servertui/__init__.py"

[tool.hatch.build.targets.wheel]
packages = ["src/servertui"]
```

- [ ] **Step 2: Create `src/servertui/__init__.py`**

```python
"""ServerTUI — Terminal dashboard for local server infrastructure."""

__version__ = "0.1.0"
```

- [ ] **Step 3: Create `src/servertui/__main__.py`**

```python
from servertui.cli import main

raise SystemExit(main())
```

- [ ] **Step 4: Create `src/servertui/cli.py` (stub — version only)**

```python
"""servertui CLI — subcommand router."""
import argparse
import sys

from servertui import __version__


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="servertui",
        description="Terminal dashboard for local server infrastructure.",
    )
    parser.add_argument("-V", "--version", action="version",
                        version=f"servertui {__version__}")
    sub = parser.add_subparsers(dest="cmd", metavar="COMMAND")

    # Subcommands will be wired in subsequent tasks.

    parser.parse_args(argv)
    parser.print_help()
    return 2


if __name__ == "__main__":
    sys.exit(main())
```

- [ ] **Step 5: Add `dist/` to `.gitignore`**

Append `dist/` to `.gitignore` so build artifacts don't get committed by subsequent `git add -A` steps. The current file has four lines (`.venv/`, `__pycache__/`, `*.pyc`, `.worktrees/`); add `dist/` on a new line at the end.

- [ ] **Step 6: Verify the package builds and runs**

Run: `uv run servertui --version`
Expected: `servertui 0.1.0`

Run: `uv build`
Expected: `dist/servertui-0.1.0.tar.gz` and `dist/servertui-0.1.0-py3-none-any.whl` created without errors.

Run: `git status --short`
Expected: `dist/` is not listed (it's now gitignored).

- [ ] **Step 7: Commit**

```bash
git add .gitignore pyproject.toml src/servertui/__init__.py src/servertui/__main__.py src/servertui/cli.py
git commit -m "Add pyproject.toml and package skeleton"
```

---

## Task 2: Move core.py and app.py into the package; wire TUI subcommand

Move the flat modules into `src/servertui/`, renaming `app.py` → `tui.py`. Rewrite the one `from core import ...` in `tui.py`. Add `tui` (and default) routing to `cli.py`.

**Files:**
- Move: `core.py` → `src/servertui/core.py`
- Move: `app.py` → `src/servertui/tui.py`
- Modify: `src/servertui/tui.py` lines 22-34 (imports block)
- Modify: `src/servertui/cli.py` (add TUI branch)

- [ ] **Step 1: Move `core.py` into the package**

```bash
git mv core.py src/servertui/core.py
```

- [ ] **Step 2: Move `app.py` to `src/servertui/tui.py`**

```bash
git mv app.py src/servertui/tui.py
```

- [ ] **Step 3: Rewrite the core import in `tui.py`**

Open `src/servertui/tui.py`. The existing import block (lines 22-34) reads:

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
    rebuild_app,
    run_cmd,
)
```

Replace the `from core import (` line with `from servertui.core import (`. Keep every imported name unchanged. The rest of the file stays untouched.

- [ ] **Step 4: Wire the TUI branch into `cli.py`**

Replace the body of `main()` in `src/servertui/cli.py` with the following (the parser setup stays the same; only the section below the `sub = ...` line changes):

```python
    sub.add_parser("tui", help="Run the TUI (default).")

    args = parser.parse_args(argv)

    if args.cmd in (None, "tui"):
        from servertui.tui import ServerTUI
        ServerTUI().run()
        return 0

    parser.print_help()
    return 2
```

- [ ] **Step 5: Verify the TUI launches via the installed entry point**

Run: `uv run servertui --version`
Expected: `servertui 0.1.0`

Run: `uv run servertui tui` (or `uv run servertui` with no args)
Expected: the TUI launches; quit with `q`.

- [ ] **Step 6: Commit**

```bash
git add -A
git commit -m "Move core/app into servertui package and wire TUI subcommand"
```

---

## Task 3: Move mcp_server.py into the package; wire MCP subcommand

Rename `mcp_server.py` → `src/servertui/mcp.py`, rewrite its core import, and route the `mcp` subcommand in `cli.py`.

**Files:**
- Move: `mcp_server.py` → `src/servertui/mcp.py`
- Modify: `src/servertui/mcp.py` lines 15-23 (imports block)
- Modify: `src/servertui/cli.py` (add MCP branch)

- [ ] **Step 1: Move the MCP server into the package**

```bash
git mv mcp_server.py src/servertui/mcp.py
```

- [ ] **Step 2: Rewrite the core import in `src/servertui/mcp.py`**

Replace the existing block (lines 15-23):

```python
from core import (
    App,
    docker_action,
    docker_container_list,
    docker_container_stats,
    fetch_app_status,
    load_apps,
    rebuild_app as core_rebuild_app,
)
```

with:

```python
from servertui.core import (
    App,
    docker_action,
    docker_container_list,
    docker_container_stats,
    fetch_app_status,
    load_apps,
    rebuild_app as core_rebuild_app,
)
```

Every imported name stays the same. The `if __name__ == "__main__": mcp.run()` block at the bottom of the file stays.

- [ ] **Step 3: Wire the MCP branch into `cli.py`**

In `src/servertui/cli.py`, add the `mcp` subparser declaration and branch. The relevant section of `main()` becomes:

```python
    sub.add_parser("tui", help="Run the TUI (default).")
    sub.add_parser("mcp", help="Run the MCP server on stdio.")

    args = parser.parse_args(argv)

    if args.cmd in (None, "tui"):
        from servertui.tui import ServerTUI
        ServerTUI().run()
        return 0

    if args.cmd == "mcp":
        from servertui.mcp import mcp
        mcp.run()
        return 0

    parser.print_help()
    return 2
```

- [ ] **Step 4: Verify `servertui mcp` starts the MCP server**

Run: `uv run servertui mcp` — the server blocks on stdin waiting for MCP messages. Press Ctrl-C to quit.
Expected: no import errors, process stays alive until interrupted.

Optional sanity check (sends an MCP `initialize` request and expects a JSON response):

```bash
printf '%s\n' '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2024-11-05","capabilities":{},"clientInfo":{"name":"test","version":"0"}}}' | uv run servertui mcp
```

Expected: a single JSON-RPC response line on stdout containing `"result"`.

- [ ] **Step 5: Commit**

```bash
git add -A
git commit -m "Move mcp_server into servertui package and wire mcp subcommand"
```

---

## Task 4: Add `servertui init` subcommand and bundle `apps.example.json`

Move `apps.example.json` into the package so it ships in the wheel, then add the `init` subcommand that scaffolds `~/.config/servertui/`.

**Files:**
- Move: `apps.example.json` → `src/servertui/apps.example.json`
- Create: `src/servertui/init.py`
- Modify: `src/servertui/cli.py` (add init branch)

- [ ] **Step 1: Move `apps.example.json` into the package**

```bash
git mv apps.example.json src/servertui/apps.example.json
```

No changes to the file contents. Hatchling automatically ships non-`.py` files that live inside the package directory; no pyproject changes needed.

- [ ] **Step 2: Create `src/servertui/init.py`**

```python
"""servertui init — scaffold ~/.config/servertui."""
from importlib.resources import files
from pathlib import Path

CONFIG_DIR = Path.home() / ".config" / "servertui"
ENV_DIR = CONFIG_DIR / "env"
APPS_JSON = CONFIG_DIR / "apps.json"


def run_init() -> int:
    CONFIG_DIR.mkdir(mode=0o700, parents=True, exist_ok=True)
    ENV_DIR.mkdir(mode=0o700, exist_ok=True)
    print(f"  ok  {CONFIG_DIR}/")
    print(f"  ok  {ENV_DIR}/")

    if APPS_JSON.exists():
        print(f"skip  {APPS_JSON} (already exists)")
    else:
        example = files("servertui").joinpath("apps.example.json").read_text()
        APPS_JSON.write_text(example)
        APPS_JSON.chmod(0o600)
        print(f"  ok  {APPS_JSON}  (copied from apps.example.json — edit me)")

    print()
    print("next:")
    print(f"  1. edit  {APPS_JSON}")
    print("  2. run   servertui")
    return 0
```

- [ ] **Step 3: Wire the `init` subcommand into `cli.py`**

The relevant section of `main()` in `src/servertui/cli.py` now becomes:

```python
    sub.add_parser("tui",  help="Run the TUI (default).")
    sub.add_parser("mcp",  help="Run the MCP server on stdio.")
    sub.add_parser("init", help="Scaffold ~/.config/servertui/.")

    args = parser.parse_args(argv)

    if args.cmd in (None, "tui"):
        from servertui.tui import ServerTUI
        ServerTUI().run()
        return 0

    if args.cmd == "mcp":
        from servertui.mcp import mcp
        mcp.run()
        return 0

    if args.cmd == "init":
        from servertui.init import run_init
        return run_init()

    parser.print_help()
    return 2
```

- [ ] **Step 4: Verify `servertui init` scaffolds into a throwaway HOME**

Run the init command against a disposable `HOME` so your real config isn't touched, then inspect the result:

```bash
TMP_HOME=$(mktemp -d)
HOME="$TMP_HOME" uv run servertui init
ls -la "$TMP_HOME/.config/servertui"
ls -la "$TMP_HOME/.config/servertui/env"
stat -f '%Lp %N' "$TMP_HOME/.config/servertui/apps.json"   # macOS
# or: stat -c '%a %n' "$TMP_HOME/.config/servertui/apps.json"  # Linux
cat "$TMP_HOME/.config/servertui/apps.json"
```

Expected: directory perms `700`, `apps.json` perms `600`, `apps.json` contents identical to `src/servertui/apps.example.json`. Running `HOME="$TMP_HOME" uv run servertui init` a second time prints `skip  .../apps.json (already exists)` and does not error.

Clean up: `rm -rf "$TMP_HOME"`.

- [ ] **Step 5: Commit**

```bash
git add -A
git commit -m "Add servertui init subcommand and bundle apps.example.json"
```

---

## Task 5: Update `run.sh` for the new dev workflow

`app.py` no longer exists at repo root, so the current `run.sh` is broken. Replace it with a thin wrapper around `uv run servertui` so contributors can launch the TUI from a fresh checkout without manual venv setup.

**Files:**
- Modify: `run.sh`

- [ ] **Step 1: Rewrite `run.sh`**

Replace the entire contents of `run.sh` with:

```sh
#!/bin/sh
# Dev launcher — runs ServerTUI from the repo checkout via uv.
# For end-user install, see install.sh or `uv tool install servertui`.
set -eu
DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$DIR"
exec uv run servertui "$@"
```

Keep the file executable (`chmod +x run.sh` if needed — the file should already be +x from the existing repo).

- [ ] **Step 2: Verify `./run.sh` still launches the TUI**

Run: `./run.sh --version`
Expected: `servertui 0.1.0`

Run: `./run.sh`
Expected: the TUI launches (quit with `q`).

- [ ] **Step 3: Commit**

```bash
git add run.sh
git commit -m "Rewrite run.sh as uv run wrapper"
```

---

## Task 6: Create the curl installer (`install.sh`)

POSIX shell script hosted in the repo, invoked as `curl -fsSL .../install.sh | sh`. Ensures uv is available, then `uv tool install --upgrade servertui`.

**Files:**
- Create: `install.sh`

- [ ] **Step 1: Write `install.sh`**

```sh
#!/bin/sh
# ServerTUI installer — https://github.com/ifarobi/servertui
set -eu

# 1. Platform check
case "$(uname -s)" in
    Linux|Darwin) ;;
    *)
        echo "error: ServerTUI only supports Linux and macOS." >&2
        exit 1
        ;;
esac

# 2. Ensure uv
if ! command -v uv >/dev/null 2>&1; then
    echo "==> uv not found; installing from astral.sh ..."
    if ! curl -LsSf https://astral.sh/uv/install.sh | sh; then
        echo "error: failed to install uv." >&2
        echo "fallback: pipx install servertui" >&2
        exit 1
    fi
    export PATH="$HOME/.local/bin:$PATH"
fi

# 3. Install / upgrade
echo "==> Installing servertui with uv ..."
uv tool install --upgrade servertui

# 4. Verify
if ! command -v servertui >/dev/null 2>&1; then
    echo "error: servertui not on PATH after install." >&2
    echo "add this to your shell rc:  export PATH=\"\$HOME/.local/bin:\$PATH\"" >&2
    exit 1
fi
servertui --version

# 5. Next steps
cat <<'EOF'

ServerTUI installed. Next steps:
  1. Scaffold your config:   servertui init
  2. Edit:                   ~/.config/servertui/apps.json
  3. Run the TUI:            servertui
  4. MCP (optional):         see https://github.com/ifarobi/servertui#mcp-server

EOF
```

- [ ] **Step 2: Make it executable**

```bash
chmod +x install.sh
```

- [ ] **Step 3: Static-check with shellcheck**

Run (skip silently if `shellcheck` isn't installed — CI or reviewers will catch it):

```bash
command -v shellcheck >/dev/null && shellcheck install.sh || echo "shellcheck not available, skipping"
```

Expected: no warnings (or a clean "skipping" line).

- [ ] **Step 4: Syntax check without executing**

Run: `sh -n install.sh`
Expected: no output, exit 0.

Note: the script can't be end-to-end tested until `servertui` is actually published to PyPI. That's the final validation step after Task 8 completes and the first release tag ships.

- [ ] **Step 5: Commit**

```bash
git add install.sh
git commit -m "Add curl installer script"
```

---

## Task 7: Create the release workflow

GitHub Actions workflow that builds the wheel/sdist and publishes to PyPI via OIDC Trusted Publishing on every `v*` tag push.

**Files:**
- Create: `.github/workflows/release.yml`

- [ ] **Step 1: Create the workflow directory**

```bash
mkdir -p .github/workflows
```

- [ ] **Step 2: Write `.github/workflows/release.yml`**

```yaml
name: release

on:
  push:
    tags: ['v*']

jobs:
  publish:
    runs-on: ubuntu-latest
    environment: pypi
    permissions:
      id-token: write        # OIDC for Trusted Publishing
    steps:
      - uses: actions/checkout@v4
      - uses: astral-sh/setup-uv@v4
      - run: uv build
      - uses: pypa/gh-action-pypi-publish@release/v1
```

- [ ] **Step 3: Verify YAML parses**

Run: `python -c "import yaml, sys; yaml.safe_load(open('.github/workflows/release.yml'))"`
Expected: no output, exit 0. (If PyYAML isn't installed globally, `uv run --with pyyaml python -c "import yaml; yaml.safe_load(open('.github/workflows/release.yml'))"` works.)

Note: end-to-end workflow verification only happens on the first actual tag push after PyPI Trusted Publishing is registered (see "Release procedure" in the spec). That's manual and outside this plan.

- [ ] **Step 4: Commit**

```bash
git add .github/workflows/release.yml
git commit -m "Add tag-driven PyPI release workflow"
```

---

## Task 8: Update `README.md`

Rewrite installation to lead with the curl one-liner / `uv tool install` / `pipx install`. Update MCP config snippet and the `apps.example.json` reference (the file is now in the package).

**Files:**
- Modify: `README.md`

- [ ] **Step 1: Rewrite the "Installation" section**

Locate the current `## Installation` section (includes the `git clone` + venv + pip install block). Replace its contents with:

```markdown
## Installation

### One-liner (recommended)

```bash
curl -fsSL https://raw.githubusercontent.com/ifarobi/servertui/main/install.sh | sh
```

This installs [uv](https://docs.astral.sh/uv/) if missing, then runs `uv tool install --upgrade servertui`. Safe to re-run — upgrades in place.

### With an existing Python tool manager

```bash
uv tool install servertui      # or: pipx install servertui
```

### First-time setup

```bash
servertui init                 # scaffolds ~/.config/servertui/
$EDITOR ~/.config/servertui/apps.json
servertui                      # launches the TUI
```

## Development

```bash
git clone git@github.com:ifarobi/servertui.git
cd servertui
./run.sh                       # uses `uv run servertui` from the repo
```
```

(The exact Markdown fencing may need to be adjusted if the README already uses a different heading style nearby — match existing conventions.)

- [ ] **Step 2: Update the MCP config snippet**

Find the JSON block under the MCP section that currently reads approximately:

```json
"servertui": {
  "command": "/path/to/servertui/.venv/bin/python",
  "args": ["mcp_server.py"],
  "cwd": "/path/to/servertui"
}
```

Replace with:

```json
"servertui": {
  "command": "servertui",
  "args": ["mcp"]
}
```

The surrounding prose ("Add to your Claude Code MCP config ...") stays. Delete the old "Install the MCP dependency" pip-install step since `mcp` is now a regular dep.

- [ ] **Step 3: Fix the `apps.example.json` reference**

Find the sentence "An example config is included in the repo as `apps.example.json`." (under the App Configuration section). Replace with:

```markdown
An example config is bundled with the package — run `servertui init` to copy it into place, or view it on [GitHub](src/servertui/apps.example.json).
```

- [ ] **Step 4: Verify by reading the file end-to-end**

Open `README.md` in a viewer and skim. Expected: no lingering references to `./run.sh` as the install path, no `pip install textual psutil docker`, no `.venv/bin/python` in the MCP config.

- [ ] **Step 5: Commit**

```bash
git add README.md
git commit -m "Rewrite README install section for PyPI distribution"
```

---

## Task 9: Update `CLAUDE.md`

Two sections change: **Run** (new entry points) and **Architecture** (file paths after the `src/` move). Also re-verify line-number references against the moved files.

**Files:**
- Modify: `CLAUDE.md`

- [ ] **Step 1: Rewrite the "Run" section**

Replace:

```markdown
## Run

```bash
./run.sh        # activates .venv and runs app.py
```

Dependencies: `textual psutil docker` on Python 3.11+. No tests, no linter, no build step.
```

with:

```markdown
## Run

```bash
servertui            # installed entry point (uv tool install servertui)
./run.sh             # dev from repo checkout (uses `uv run servertui`)
```

Subcommands: `servertui tui` (default), `servertui mcp`, `servertui init`. Dependencies: `textual psutil docker mcp` on Python 3.11+. Build backend: hatchling.
```

- [ ] **Step 2: Rewrite the "Architecture" opening paragraph**

Replace:

```markdown
Shared logic lives in `core.py`; the TUI is in `app.py`; the MCP server is in `mcp_server.py`. Key pieces:
```

with:

```markdown
All code lives in the `src/servertui/` package. Shared logic is in `core.py`; the TUI in `tui.py`; the MCP server in `mcp.py`; the CLI router in `cli.py`; `servertui init` in `init.py`. Key pieces:
```

- [ ] **Step 3: Re-verify the `DataStore` and `ServerTUI` line numbers**

The current CLAUDE.md cites `DataStore` at line 119 and `ServerTUI` at line 509 in `app.py`. After the import rewrite (Task 2, step 3) the file moves to `src/servertui/tui.py` and only one line changes (the `from core import (` → `from servertui.core import (`), which does not shift line counts. Verify with:

```bash
grep -n '^class DataStore' src/servertui/tui.py
grep -n '^class ServerTUI' src/servertui/tui.py
```

Update the numbers in CLAUDE.md if they differ from 119 / 509.

- [ ] **Step 4: Commit**

```bash
git add CLAUDE.md
git commit -m "Update CLAUDE.md for src/ layout and new entry points"
```

---

## Task 10: Update `AGENTS.md`

Single change: the intro line that mentions `mcp_server.py`.

**Files:**
- Modify: `AGENTS.md`

- [ ] **Step 1: Update the MCP intro line**

Replace:

```markdown
ServerTUI provides an MCP server (`mcp_server.py`) for managing apps and Docker containers programmatically. If the MCP server is configured, prefer using these tools over shell commands for server operations.
```

with:

```markdown
ServerTUI provides an MCP server (`servertui mcp`, implemented in `src/servertui/mcp.py`) for managing apps and Docker containers programmatically. If the MCP server is configured, prefer using these tools over shell commands for server operations.
```

The rest of the file (tool table, workflows, tips) is unchanged — tool names and behavior are preserved.

- [ ] **Step 2: Commit**

```bash
git add AGENTS.md
git commit -m "Update AGENTS.md for new MCP entry point"
```

---

## Task 11: Final end-to-end verification

Confirm the full install flow works from a clean wheel, outside the repo checkout.

**Files:** none modified.

- [ ] **Step 1: Build the wheel**

Run: `uv build`
Expected: `dist/servertui-0.1.0-py3-none-any.whl` and `dist/servertui-0.1.0.tar.gz` exist.

- [ ] **Step 2: Install the wheel into a throwaway uv tool env**

```bash
uv tool install --force ./dist/servertui-0.1.0-py3-none-any.whl
```

Expected: clean install, `servertui` now on `$PATH`.

- [ ] **Step 3: Smoke-test each subcommand**

```bash
servertui --version                         # -> servertui 0.1.0
TMP_HOME=$(mktemp -d); HOME="$TMP_HOME" servertui init
ls -la "$TMP_HOME/.config/servertui"        # dir 700, apps.json 600
servertui --help                            # shows tui / mcp / init
rm -rf "$TMP_HOME"
```

Expected: all commands succeed, output matches the comments.

- [ ] **Step 4: Confirm `apps.example.json` is bundled in the wheel**

```bash
unzip -l dist/servertui-0.1.0-py3-none-any.whl | grep apps.example.json
```

Expected: one line showing `servertui/apps.example.json` inside the wheel.

- [ ] **Step 5: Uninstall the test copy**

```bash
uv tool uninstall servertui
```

- [ ] **Step 6: No commit**

This task only verifies — the repo state is already correct from prior commits. `dist/` was added to `.gitignore` in Task 1 Step 5, so build artifacts won't appear in `git status`.

---

## Out of scope (deferred to a separate task)

- First actual PyPI release (one-time Trusted Publisher registration on pypi.org + `git tag v0.1.0 && git push --tags`).
- Homebrew formula / tap.
- Dev dependency group, tests, linter.
- `servertui doctor` / diagnostics subcommand.
- Changelog automation.
