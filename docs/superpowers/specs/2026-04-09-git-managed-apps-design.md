# Git-Managed Apps

## Context

ServerTUI currently requires users to manually clone git repos and point `repo_path` at them in `apps.json`. This means setup involves multiple steps: clone the repo, note the path, edit the config. It also couples app config to the user's filesystem layout.

This change makes ServerTUI own the clone lifecycle. Users provide a `git_url` and ServerTUI handles cloning into a managed directory (`~/servertui/apps/<name>/`). This simplifies setup to: add a JSON entry, start ServerTUI.

## Config Format

**File:** `~/.config/servertui/apps.json`

```json
[
  {
    "name": "foo",
    "git_url": "git@github.com:user/foo.git",
    "tunnel": "foo",
    "branch": "main"
  },
  {
    "name": "bar",
    "git_url": "https://github.com/user/bar.git",
    "compose_file": "docker-compose.staging.yml"
  }
]
```

**Fields:**
| Field | Required | Description |
|-------|----------|-------------|
| `name` | yes | App display name. Also determines clone dir and container name (`servertui-<name>`) |
| `git_url` | yes | Git remote URL (SSH or HTTPS) |
| `tunnel` | no | Bare Cloudflare tunnel name for cross-reference |
| `branch` | no | Branch to clone/checkout. If omitted, uses repo default branch |
| `compose_file` | no | Override compose filename, relative to repo root |

## Managed Directory

- **Default:** `~/servertui/apps/`
- **Override:** `SERVERTUI_APPS_DIR` environment variable
- **Structure:** `<APPS_DIR>/<app.name>/` — one subdirectory per app containing the git clone

The directory is created automatically if it doesn't exist.

## App Dataclass Changes

```python
@dataclass(frozen=True)
class App:
    name: str
    git_url: str
    tunnel: str | None = None
    branch: str | None = None
    compose_file: str | None = None

    @property
    def repo_path(self) -> Path:
        return APPS_DIR / self.name
```

`repo_path` becomes a computed property. All existing code that reads `app.repo_path` continues to work unchanged.

## Clone Behavior

### On Startup (auto-clone)

For each app in the config, if `app.repo_path` doesn't exist:

1. Run `git clone <git_url> <repo_path>` (with `--branch <branch>` if specified)
2. Clone runs in a background thread (same pattern as existing `bg_fetch_expensive`)
3. During clone, the app's status in the panel shows "cloning..."
4. On success, normal status monitoring begins
5. On failure (bad URL, auth, network), status shows "clone failed: <reason>"

### On Rebuild (R key, existing flow)

No change to the existing rebuild pipeline. It already does `git pull --ff-only` followed by docker build/compose. The only difference is the working directory is now inside the managed apps dir.

## Load Function Changes

`load_apps()` validates:
- `name` is a non-empty string
- `git_url` is a non-empty string
- `branch` is a string if present
- `tunnel` is a string if present
- `compose_file` is a string if present

No longer validates or expands `repo_path` (it's computed).

## AppInfo Changes

`AppInfo.git_state` gains a new possible value: `"cloning"` while a clone is in progress, and `"clone-failed"` if the clone failed. The panel rendering handles these states.

## Files to Modify

| File | Change |
|------|--------|
| `app.py` (~line 47) | Update `App` dataclass: replace `repo_path` field with `git_url` + `branch`, add `repo_path` property |
| `app.py` (~line 55) | Add `APPS_DIR` constant with env var override |
| `app.py` (~line 60) | Update `load_apps()` to validate `git_url`/`branch` instead of `repo_path` |
| `app.py` (new) | Add `clone_if_missing(app)` function |
| `app.py` (startup) | Call `clone_if_missing` for each app on startup, in background threads |
| `app.py` (AppInfo) | Add "cloning" and "clone-failed" git_state values |
| `app.py` (panel render) | Handle new git_state values in display |
| `apps.example.json` | Update to new format with `git_url` |

## Verification

1. Update `apps.example.json` with sample git URLs
2. Create a test `apps.json` with a public repo git URL
3. Start ServerTUI — verify it auto-clones into `~/servertui/apps/<name>/`
4. Verify the app panel shows "cloning..." then transitions to normal status
5. Trigger rebuild (R) — verify `git pull` + build works from the managed directory
6. Test with `branch` field — verify correct branch is checked out
7. Test with bad URL — verify "clone failed" status appears, TUI stays responsive
8. Test `SERVERTUI_APPS_DIR` override — verify clones go to custom location
