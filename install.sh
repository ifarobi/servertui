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
