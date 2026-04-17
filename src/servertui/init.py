"""servertui init — scaffold ~/.config/servertui."""
import sys
from importlib.resources import files

from servertui.core import APPS_CONFIG as APPS_JSON, CONFIG_DIR, ENV_DIR


def run_init() -> int:
    try:
        CONFIG_DIR.mkdir(mode=0o700, parents=True, exist_ok=True)
        ENV_DIR.mkdir(mode=0o700, exist_ok=True)
        print(f"  ok  {CONFIG_DIR}/")
        print(f"  ok  {ENV_DIR}/")

        if APPS_JSON.exists():
            print(f"skip  {APPS_JSON} (already exists)")
        else:
            example = files("servertui").joinpath("apps.example.json").read_text(encoding="utf-8")
            APPS_JSON.write_text(example, encoding="utf-8")
            APPS_JSON.chmod(0o600)
            print(f"  ok  {APPS_JSON}  (copied from apps.example.json — edit me)")
    except OSError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1

    print()
    print("next:")
    print(f"  1. edit  {APPS_JSON}")
    print("  2. run   servertui")
    return 0
