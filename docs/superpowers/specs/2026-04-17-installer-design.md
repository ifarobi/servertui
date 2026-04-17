# ServerTUI Installer — Design

**Date:** 2026-04-17
**Status:** Approved, pending implementation plan
**Audience:** Public-ish distribution — strangers who find the GitHub repo should be able to install ServerTUI in one command.

## Goals

- A clean `uv tool install servertui` / `pipx install servertui` experience sourced from PyPI.
- A single `curl -fsSL .../install.sh | sh` one-liner that handles install *and* upgrade idempotently.
- A `servertui` CLI with room to grow (subcommands), documented MCP config that fits on one line.
- A `servertui init` command that scaffolds `~/.config/servertui/` so first-run doesn't require hand-editing paths.
- An automated tag-driven release workflow that doesn't require long-lived PyPI tokens.

## Non-Goals (v1)

- Homebrew formula / tap. Deferred until there's demonstrable demand; `uv tool install` covers the audience.
- Optional dependency extras (e.g. `servertui[mcp]`). MCP is a first-class feature and ships with the base install.
- Dev-dependency group, test harness, linter config. Add when there's something to install.
- Changelog automation, release-please, pre-release channels.
- Windows support. Tool requires `systemctl --user` and Docker; staying Linux/macOS-only.

## Decisions

| # | Decision | Rejected alternatives |
|---|----------|-----------------------|
| 1 | Public distribution (C) | Self-only / small-circle |
| 2 | PyPI + curl installer; defer Homebrew (B) | All three / curl-only / uv-only |
| 3 | Single `servertui` command with subcommands (A) | Two console scripts / TUI-only |
| 4 | MCP is a required dependency (B) | Optional extra |

## Package Layout

Repo moves from a flat layout to `src/` layout with an installable package:

```
servertui/                   ← repo root
├── pyproject.toml           ← NEW: build metadata, deps, entry point
├── README.md
├── AGENTS.md
├── CLAUDE.md
├── install.sh               ← NEW: curl-able bootstrap script
├── run.sh                   ← kept for dev-from-checkout; invokes `python -m servertui`
├── .github/workflows/release.yml  ← NEW: tag-driven PyPI publish
└── src/
    └── servertui/
        ├── __init__.py      ← holds __version__
        ├── __main__.py      ← enables `python -m servertui`
        ├── cli.py           ← argparse router
        ├── core.py          ← moved from repo root, imports rewritten to servertui.core
        ├── tui.py           ← was app.py
        ├── mcp.py           ← was mcp_server.py
        ├── init.py          ← NEW: `servertui init` implementation
        └── apps.example.json  ← moved from repo root, bundled as package data
```

Import rewrites are the only code change: `from core import ...` → `from servertui.core import ...` in `tui.py` and `mcp.py`. The `src/` layout is chosen to prevent accidentally importing from the repo instead of the installed package during development.

`run.sh` is kept for contributor ergonomics and rewritten to invoke `uv run servertui` from the repo — this picks up `pyproject.toml`, manages the venv, and runs the installed entry point without requiring the contributor to do a manual editable install. The existing `.venv/`-based workflow is deprecated (the venv directory stays git-ignored; contributors who want one can still create it and `uv pip install -e .`).

## `pyproject.toml`

Build backend: **hatchling**. Modern default, zero config, works with uv.

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

**Design calls:**

- Version is single-sourced from `src/servertui/__init__.py` (`__version__ = "0.1.0"`). No `__version__.py` sidecar.
- Lower-bound version pins only. Upper bounds break venv co-installation; add only when a real breakage is observed.
- One `[project.scripts]` entry — `servertui`. All subcommands route through `servertui.cli:main`.
- Starting version is `0.1.0`.

## CLI Router (`src/servertui/cli.py`)

Stdlib argparse. Subcommands: `tui` (default), `mcp`, `init`. No third-party CLI framework.

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


if __name__ == "__main__":
    sys.exit(main())
```

`src/servertui/__main__.py` is one line so `python -m servertui` works identically to the installed script:

```python
from servertui.cli import main
raise SystemExit(main())
```

**Design calls:**

- `servertui` with no args runs the TUI. No UX regression vs. today's `./run.sh`.
- Imports are lazy inside each branch. `servertui --version` doesn't import textual/docker/mcp — fast CI smoke test.
- No `doctor` / diagnostic subcommand in v1. Easy to add later.

## `servertui init` (`src/servertui/init.py`)

Idempotent scaffolding of the user's config directory.

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

**Design calls:**

- Safe to re-run — directories use `exist_ok=True`; `apps.json` is never overwritten.
- Directory perms 0700, file perms 0600 — consistent with existing env-file convention and keeps secrets pasted into `apps.json` from leaking.
- No `--force` flag. Users can `rm apps.json && servertui init` to reset. One less failure mode.

## Curl Installer (`install.sh`)

POSIX shell, hosted in the repo, invoked as:

```
curl -fsSL https://raw.githubusercontent.com/ifarobi/servertui/main/install.sh | sh
```

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

**Design calls:**

- `/bin/sh` + `set -eu` (no `pipefail` — not POSIX; macOS `sh` is minimal).
- `uv tool install --upgrade` is the idempotency trick — same script installs and upgrades.
- No `sudo`, no system-wide install. Everything lives under `$HOME/.local/`.
- URL is always `main/install.sh`. No versioned installer URL in v1.
- Fallback hint (`pipx install servertui`) is printed only if uv install itself fails; we don't branch the install path.

## Release Workflow (`.github/workflows/release.yml`)

Tag-driven publish to PyPI via Trusted Publishing (OIDC — no long-lived token).

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

**Release procedure** (to be documented in `README.md`):

1. Bump `__version__` in `src/servertui/__init__.py`.
2. `git commit -am "Release v0.1.0"`.
3. `git tag v0.1.0 && git push --tags`.
4. Wait ~1 min for the workflow; confirm on pypi.org.

**One-time PyPI setup** (prerequisite, not part of this spec's code changes): register `servertui` on pypi.org as a Trusted Publisher pointing at `ifarobi/servertui`, workflow `release.yml`, environment `pypi`. This is a UI-only action on pypi.org.

**Design calls:**

- Tag push is the single source of truth for "this version is published". No manual dispatch.
- No changelog automation. Hand-edited `CHANGELOG.md` can come later.
- No pre-release / RC channel. Single-track `vX.Y.Z`.

## Documentation Updates

- **README.md** — rewrite the "Installation" section to lead with the curl one-liner, followed by `uv tool install servertui` and `pipx install servertui`. Remove the manual `git clone` + `venv` + `pip install` instructions from the install path (keep them in a "Development" section). Update the MCP config snippet to `"command": "servertui", "args": ["mcp"]`. Update the `apps.example.json` reference — the file has moved into the package; link to it on GitHub (`src/servertui/apps.example.json`) or instruct users to run `servertui init`.
- **CLAUDE.md** — two sections change:
  - **Run** — swap the `./run.sh` note for `servertui` (installed) as the primary path and `./run.sh` / `uv run servertui` for the dev path.
  - **Architecture** — update all file paths from flat layout to `src/servertui/`: `core.py` → `src/servertui/core.py`, `app.py` → `src/servertui/tui.py` (note the rename), `mcp_server.py` → `src/servertui/mcp.py`. The line-number references (`DataStore` line 119, `ServerTUI` line 509) should be re-verified against the moved file and updated if the import rewrite shifted them.
- **AGENTS.md** — update the intro line that references `mcp_server.py` to point at `src/servertui/mcp.py` (or drop the path — the tool surface is what matters, not the filename). Tool behavior is unchanged.

## Risks / Open Questions

- **PyPI name squatting.** `servertui` is currently unclaimed on pypi.org (verify before first release). If taken, fall back to `servertui-cli` or similar.
- **uv installer URL stability.** `astral.sh/uv/install.sh` is the documented official URL. If Astral ever changes it, `install.sh` needs a bump — acceptable risk.
- **Textual version floor.** 0.60 is a guess; the installed `.venv` version should be used as the actual floor. Verify during implementation by running `uv pip show textual psutil docker mcp` in the existing `.venv` and setting floors to match.

## Out of Scope (explicit)

- Homebrew tap. If added later, the formula will wrap `uv tool install` or use `Formula.virtualenv` — no change to PyPI publish flow.
- Self-update subcommand. `uv tool upgrade servertui` or re-running the curl script handles this.
- Config migration / schema versioning for `apps.json`. Current schema is simple enough to not need it.
