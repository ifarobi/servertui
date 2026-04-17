#!/bin/sh
# Dev launcher — runs ServerTUI from the repo checkout via uv.
# For end-user install, see install.sh or `uv tool install servertui`.
set -eu
DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$DIR"
exec uv run servertui "$@"
