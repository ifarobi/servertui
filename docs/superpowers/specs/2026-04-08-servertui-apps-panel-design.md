# ServerTUI Apps Panel — Design

**Date:** 2026-04-08
**Status:** Approved for planning
**Scope:** v1 of an "Apps" concept for ServerTUI — manual, on-demand rebuilds of locally-cloned app repos, plus env file management. Deliberately *not* a deploy engine.

## Motivation

ServerTUI currently manages the infrastructure surrounding apps (Cloudflare tunnels, Docker containers, systemd timers, host stats) but not the apps themselves. Day-to-day, updating an app means SSHing in, `cd`-ing to a repo, `git pull`, rebuilding an image, restarting a container — ten keystrokes minimum, across multiple tools. This design collapses that into one keypress while keeping ServerTUI's identity as a **read+control dashboard for what's already on the box**, not a PaaS.

Explicitly *not* in scope: auto-deploy from git, webhooks, rollback, health checks, deploy history, remote repos, secrets beyond env files. Those can be added later as isolated additions if the manual flow proves insufficient. We build the one-keystroke manual version first and see whether we actually miss the rest.

## Non-goals

- Competing with Coolify, Dokploy, Watchtower, or CI systems.
- Polling git remotes or reacting to pushes.
- Managing apps on machines other than localhost.
- Orchestrating multi-service deployments beyond `docker compose up -d --build`.
- Managing secrets other than env vars (TLS certs, SSH keys, service-account JSON are out).
- Editing env values inside the TUI with a form widget.

## User experience

A new **Apps** tab joins the existing Tunnels/Docker/Ollama/Timers tabs. It shows a table of apps declared in `app.py`, each row displaying:

- Name
- Container status (running / stopped / missing) with the same emoji convention used elsewhere
- Image tag + short uptime
- Linked tunnel name and its status (cross-referenced from the existing tunnel data)
- Git state of the local repo: `clean`, `dirty`, or `behind N` (cheap: `git status --porcelain` + `git rev-list --count HEAD..@{u}`, best-effort, failures render as `?`)
- Env key count (names only, never values) or `⚠ perms` if the env file permissions are looser than `600`

Keybindings on the Apps tab:

- `R` — **Rebuild** the selected app (git pull → build → restart). Streams output into a modal.
- `E` — **Edit env file** for the selected app. Suspends the TUI, launches `$EDITOR`, resumes on exit, offers to restart the container.
- `L` — **Logs** for the selected app's container (`docker logs -f`), reusing the existing `LogScreen`.
- `f` — Force refresh (already global).

## Configuration

Apps are declared as a list at the top of `app.py`, next to `TUNNELS`:

```python
@dataclass(frozen=True)
class App:
    name: str           # display name + container name (prefixed servertui-)
    repo_path: Path     # local clone, absolute
    tunnel: str | None  # optional cross-reference to a TUNNELS entry
    # No git URL, no branch, no port — ServerTUI drives what's on disk.

APPS: list[App] = [
    App(name="foo", repo_path=Path("/srv/foo"), tunnel="foo"),
]
```

Rationale for keeping config in `app.py`:
- Matches the existing `TUNNELS` pattern — zero new concepts for the reader.
- No TOML parser, no config-file search path, no schema validation.
- When the list gets annoying to edit in-place, *then* extract it to a file. Not before.

The container for app `foo` is always named `servertui-foo`. ServerTUI identifies "its" containers by this prefix and will never touch containers it didn't name.

## Rebuild action (`R`)

Runs in a background daemon thread so the TUI stays responsive. Pipeline:

1. **Detect build shape** by checking for files in `repo_path`:
   - `compose.yml` or `docker-compose.yml` → **compose mode**
   - else `Dockerfile` → **dockerfile mode**
   - else → error, abort, show message
2. **`git pull --ff-only`** in `repo_path`. On failure (non-fast-forward, dirty tree, network), abort and surface the error. No auto-stash, no force.
3. **Build + restart:**
   - Dockerfile mode:
     ```
     docker build -t servertui-<name> <repo_path>
     docker rm -f servertui-<name>   # only after build succeeds
     docker run -d --name servertui-<name> \
         --env-file ~/.config/servertui/env/<name>.env \
         --restart unless-stopped \
         servertui-<name>
     ```
     Port publishing and any other `docker run` flags are intentionally omitted in v1 — apps that need external ports should use compose mode, where flags live in the compose file. (This keeps the `App` dataclass tiny and avoids inventing a port-mapping schema.)
   - Compose mode:
     ```
     docker compose -f <repo_path>/compose.yml up -d --build
     ```
     `--env-file` is passed to `docker compose` if the env file exists. Compose handles per-service env wiring.
4. **Stream stdout+stderr** line-by-line into a modal `BuildScreen` (a variant of the existing `LogScreen` that reads from a `subprocess.Popen` pipe instead of `journalctl`). Modal shows a final `[exit 0]` / `[exit N]` line and stays open until dismissed.

**Failure semantics (the closest thing to a "rollback" in v1):** because `docker rm -f` runs *after* `docker build` succeeds, a build failure leaves the currently-running container untouched. A successful build followed by a failed `docker run` leaves you with no container running — the TUI shows this honestly as "stopped" and you can press `R` again. No magic recovery, but no silent breakage either.

**Concurrency:** only one rebuild at a time, globally. If `R` is pressed while a rebuild is in flight, show a message and ignore. Simpler than a build queue and matches the "this is my box, I'm one person" constraint.

## Env file management (`E`)

- **Path:** `~/.config/servertui/env/<name>.env`
- **Format:** standard dotenv. ServerTUI never parses values — it only enumerates keys for display.
- **Permissions:** on first write, ServerTUI creates `~/.config/servertui/env/` as `700` and the file as `600`. On every read, if the file's mode is looser than `600`, ServerTUI refuses to use it and shows `⚠ perms` in the panel. The user can `chmod` it themselves; ServerTUI does not silently fix permissions.
- **Editing (`E` keybind):** ServerTUI suspends the Textual app (`app.suspend()`), creates the file if missing with `600`, launches `$EDITOR` (falling back to `nano`, or aborting with a message if neither is resolvable), waits for the editor to exit, then resumes the TUI. This is the same mechanism `git commit` uses — it works on headless servers because `$EDITOR` is itself a terminal program sharing the same tty. No GUI, no X11, no second pane needed.
- **After editing:** ServerTUI re-reads the key list for the panel and shows a small prompt: *"Env updated. Restart container? [y/N]"*. `y` triggers a container restart (not a full rebuild — just `docker restart servertui-<name>` in dockerfile mode, or `docker compose restart` in compose mode) so the new env is picked up.
- **Never logged, never rendered.** Only key *names* are ever shown in the UI. Values live exclusively on disk.

## Data flow

A new `AppInfo` dataclass joins the existing state types in `DataStore`:

```python
@dataclass
class AppInfo:
    name: str
    container_status: str          # running/stopped/missing
    image: str | None
    uptime: str | None
    tunnel: str | None
    git_state: str                 # clean/dirty/behind N/?
    env_key_count: int | None
    env_perms_ok: bool
    build_mode: str                # dockerfile/compose/none
```

`DataStore.apps: dict[str, AppInfo]` is populated by a new cheap fetcher that runs on the existing 2s foreground tick (or the 15s cheap interval — whichever is less disruptive; we'll land on one during implementation). All the data sources are already cheap:
- Container status reuses the data the Docker panel already fetches.
- Tunnel cross-reference is an in-memory lookup.
- `git status --porcelain` + `rev-list` are fast on local repos.
- Env file stat + line count is trivial.

No new background thread is needed for the v1 feature set. Rebuilds are one-shot threads spawned on demand, not long-lived workers.

## Components and boundaries

The change touches these units, each with a single responsibility:

- **`App` dataclass + `APPS` list** — declarative configuration, no behavior.
- **`AppInfo` + fetcher function** — produces a snapshot of each app's state from the filesystem, Docker, and the existing tunnel data. Pure read.
- **`AppPanel(Static)`** — renders `DataStore.apps`. No side effects, no subprocess calls. Mirrors the other panels.
- **`rebuild_app(app: App) -> Iterator[str]`** — generator that yields output lines from the git/build/run pipeline. Pure orchestration; calls existing `run_cmd` helpers.
- **`BuildScreen(ModalScreen)`** — consumes the generator, displays lines, shows exit code. Variant of `LogScreen`.
- **`edit_env_file(app: App)`** — suspends the app, launches `$EDITOR`, resumes. Returns whether the file changed (by mtime comparison).
- **`ServerTUI` action methods** — `action_app_rebuild`, `action_app_edit_env`, `action_app_logs`. Thin dispatch only.

Each of these can be reasoned about and tested in isolation without reading the rest of `app.py`. The rebuild generator in particular is deliberately decoupled from the modal so the build logic doesn't know or care that a UI exists.

## Error handling

- **Missing repo path** → app renders as `⚠ missing` in the panel, rebuild is a no-op with an error toast.
- **`git pull` fails** → build aborts, `BuildScreen` shows git's error output, existing container is untouched.
- **`docker build` fails** → same: existing container untouched, error shown.
- **`docker run` fails after a successful build + rm** → container is stopped; panel reflects reality; user can retry with `R`.
- **`$EDITOR` unset and `nano` missing** → `E` shows a message: *"Set $EDITOR to a terminal editor."* No fallback to a built-in editor.
- **Env file perms too loose** → panel shows `⚠ perms`, rebuild refuses to pass `--env-file`, error is explicit.
- **Concurrent rebuild attempt** → ignored with a brief message.

All of these are visible in the UI; none of them silently corrupt state.

## Testing

Consistent with the rest of the project: no formal test suite exists, and this design doesn't add one. Manual verification steps will live in the implementation plan: declare a test app pointing at a throwaway local repo, rebuild it, edit its env file, rebuild again, break it deliberately, observe the panel.

## Open questions deferred to implementation

- Exact refresh cadence for the apps panel (2s foreground tick vs. folding into the 15s cheap fetcher).
- Whether to show the last rebuild's exit code inline in the panel row or only in the modal.
- Whether `R` should confirm when the git state is `dirty` (probably yes — a quick `[y/N]` prompt).

These are small enough to decide while writing the code, not now.

## What this unlocks later (non-committal)

If the manual flow proves insufficient after real use, any of these can be added as *isolated* features without redesigning the core:

- Auto-rebuild on a timer (reuse the existing cheap fetcher thread, add a `poll_interval` field to `App`).
- Remote repo URLs + auto-clone.
- Multi-container inspection in compose mode.
- Rebuild history (append-only log file per app).
- Secrets beyond env vars.

None of these are committed to. Each would get its own brainstorm if and when it becomes real.
