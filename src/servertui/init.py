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
