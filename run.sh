#!/bin/bash
DIR="$(cd "$(dirname "$(readlink -f "$0")")" && pwd)"
source "$DIR/.venv/bin/activate"
python "$DIR/app.py" "$@"
