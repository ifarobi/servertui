# Multi-`.env` Files per App (directory model)

**Status:** draft
**Date:** 2026-05-04
**Builds on / supersedes pieces of:** PR #2 (first-edit seeding), `docs/superpowers/specs/2026-04-17-multi-env-import-design.md` (import flow keeps working but its target path changes)

## Problem

Compose deployments routinely need **multiple env files at specific paths in the repo**:

- `env_file: [.env, .env.production]` in `compose.yml` requires both files to physically exist in the repo.
- Next.js (and similar) distinguish `.env` (runtime) from `.env.production` (build-time `ARG`s).
- BE+FE in one repo with separate concerns (e.g. `.env` for the API, `.env.web` for the frontend service).

The current model is a **single canonical file** at `~/.config/servertui/env/<name>.env`:

- Dockerfile mode: `docker run --env-file <canonical>` — works.
- Compose mode: prints a yellow warning that the canonical is **not auto-wired** (`src/servertui/core.py:720`). The user is expected to manually reference the canonical path in their compose file or set up symlinks themselves. In practice this leaves compose deployments broken until the user hand-wires env access — which conflicts with `~/.claude/CLAUDE.md`'s "never edit deployed checkouts" rule (the deployed checkout is exactly where the wiring would have to live).

The recently shipped `I=import` action (PR #4 / 2026-04-17) merges keys from repo `.env*` files into the canonical, but it does not solve the compose problem: even if every key is imported, the compose file still references file *paths* that don't exist.

## Non-goals

- Two-way sync between managed files and repo files. Repo `.env*` files we encounter are still treated as read-only sources — we never write into them, only over-symlink with checks.
- Schema changes to `apps.json`. The directory layout is convention-driven.
- Per-key reconciliation across files. The user owns what goes in each file; ServerTUI just manages storage and wiring.

## User-facing behavior

### Storage model

Per-app directory: `~/.config/servertui/env/<name>/` containing arbitrary `.env*` files (`.env`, `.env.production`, `.env.local`, `.env.web`, …).

- The directory is created `0o700` on first use.
- Each file is created `0o600` and refused for use if perms drift.
- Legacy `~/.config/servertui/env/<name>.env` is auto-migrated to `<name>/.env` on first read or first edit. One-shot, idempotent, no user action required.

### Dockerfile mode

Each managed file in the app's env dir is passed as a separate `--env-file` to `docker run`. Order is filename-sorted (so `.env` precedes `.env.production`); Docker applies later files on top of earlier ones.

### Compose mode

Before `docker compose up`, ServerTUI symlinks each managed file into the repo:

```
<repo>/<filename> -> ~/.config/servertui/env/<app>/<filename>
```

This satisfies both `env_file:` entries and compose's `${VAR}` interpolation with no special compose flags.

Pre-flight checks (refuse to deploy if any fails, with a clear message):

1. **Filename safety:** only files matching `.env*` are ever created in the repo. No path traversal, no other prefixes.
2. **Git-tracked collision:** `git ls-files --error-unmatch <filename>` — if a real (non-symlink) tracked file already exists at the symlink target, abort with `"<file> is tracked in git -- refusing to overwrite"`. Future `git pull --ff-only` would otherwise fail.
3. **Permissions:** existing 600-perm check applied per file.

If the symlink already points at the right managed file, leave it alone (idempotent rebuilds).

### `E` shortcut (edit env)

Three-screen flow:

1. App picker (existing).
2. **File picker** (new): shows
   - existing managed files: `<name>  [edit]`
   - missing common presets: `.env / .env.local / .env.production / .env.development / .env.staging`, marked `[new]`
3. `$EDITOR` opens the chosen file (created `0600` if new). Existing post-edit perms recheck and "restart container?" prompt unchanged.

If a user wants a non-preset name (e.g. `.env.web`), they can either pick a preset and rename later, or use `I=import` from a repo file with that name (which auto-creates the matching managed file — see below). Custom-name prompt is deferred to a follow-up.

### `I` shortcut (import env)

The action is preserved with one semantic change: imports now target the matching filename in the per-app dir.

- Importing from `<repo>/.env.production` → merges into `~/.config/servertui/env/<name>/.env.production` (auto-create if missing).
- The per-key `ImportKeysScreen` is unchanged. "Replaces" markers compare against the keys already in the matching managed file.
- One-source-one-destination is the new default. The "merge everything into one canonical" behavior is dropped — but is recoverable by importing from each source into the same destination filename, if a user really wants that.

### Status panel

`env: N keys` aggregates across all files in the app's dir. The perms-warning fires if any file is non-600.

### Footer / keybindings

Unchanged: `R/E/I/L`. Only the *behavior* of `E` and `I` shifts; the keys stay.

### `CLAUDE.md` and `README.md`

Update the env-files section in both to describe the directory model and the auto-migration. Note the "won't symlink over tracked files" guarantee.

## Architecture

### `src/servertui/core.py` — pure helpers

**`env_dir_for(app_name: str) -> Path`** — returns `ENV_DIR / app_name`.

**`migrate_legacy_env(app_name: str) -> None`** — one-shot move of `ENV_DIR/<name>.env` (regular file, not symlink) to `ENV_DIR/<name>/.env`. Creates dir 0700, file mode preserved. Silent no-op if already migrated, target exists, or any OSError.

**`list_env_files(app_name: str) -> list[Path]`** — sorted list of `.env*` regular files (no symlinks, no subdirs) in the app's env dir. Empty list if dir missing.

**`inspect_env_dir(app_name: str) -> tuple[int | None, bool]`** — replaces the per-call `inspect_env_file(ENV_DIR / f"{name}.env")`. Returns `(total_key_count, all_perms_ok)`. `None` count means "no managed files yet."

**`wire_env_into_repo(app: App, repo_path: Path) -> tuple[bool, list[str]]`** — performs the symlink dance for compose mode. Returns `(ok, messages)`. Encapsulates the safety checks (filename pattern, git-tracked, perms). Called from `rebuild_app`'s compose branch.

### `src/servertui/core.py` — `rebuild_app` changes

- At entry: `migrate_legacy_env(app.name)`; collect `env_files = list_env_files(app.name)`.
- Perms-check loop: refuse if any file is non-600 (existing behavior, applied per-file).
- Dockerfile branch: `for ef in env_files: cmd += ["--env-file", str(ef)]`.
- Compose branch: call `wire_env_into_repo`; abort on failure; then `docker compose up -d --build` unchanged.

The yellow "compose mode -- not auto-wired" warning is removed.

### `src/servertui/tui.py` — UI

- `edit_env_file(app_cfg, filename: str = ".env")` — accepts a target filename; validates `filename.startswith(".env")` and rejects `/`, `.`, `..`. Creates `<app_dir>/<filename>` 0600 if missing. Otherwise unchanged.
- `action_app_edit_env` — adds the file-picker step between app picker and editor.
- `action_app_import_env` — destination changes from `ENV_DIR/<name>.env` to `env_dir_for(name) / source_filename`.
- `ImportKeysScreen` — unchanged structurally; "replaces" detection now reads the matching destination file rather than the canonical.

### `src/servertui/mcp.py`

No tool surface changes. `get_app_status` already returns aggregate `env_key_count` / `env_perms_ok` via `AppInfo`; the new aggregation in `inspect_env_dir` produces the same shape.

## Migration

Triggered automatically on first read in `fetch_app_status` and on first edit in `edit_env_file`:

- If `ENV_DIR/<name>.env` is a regular file and `ENV_DIR/<name>/` does not exist or is empty: create dir 0700, `os.rename()` the file to `<name>/.env`. Atomic — same filesystem.
- If both already exist (someone manually created the dir): leave the legacy file alone; user resolves manually. Rare; logged to stderr.

No version bump-driven migration. Idempotent and lazy.

## Error handling

| Case | Behavior |
|---|---|
| `repo_path` missing | existing "repo not cloned yet" error path, unchanged |
| Managed file with non-600 perms | abort with existing message, naming the offending file |
| Compose mode + repo has tracked `.env` | abort with `"<repo>/.env is tracked in git -- refusing to overwrite"` and a hint to `git rm --cached` or rename the managed file |
| `os.symlink` raises (e.g. read-only FS) | warn `"could not symlink <path>: <e>"` and abort the rebuild |
| Legacy migration fails | log to stderr, leave both old and new in place; rebuild continues using whichever the new code path finds |
| User picks a non-`.env*` preset (impossible via UI; defensive only) | `edit_env_file` returns `(False, "invalid env filename")` |

All errors continue to surface via `notify(..., severity="error")` in the TUI; no exceptions reach Textual's event loop.

## Security

- Managed files remain `0o600`; managed dir `0o700`. Existing TOCTOU-safe creation pattern preserved.
- Symlinks in the repo point *out* of the repo into the managed dir — readable only by the user. Compose / Docker reads them through the user's own UID, so no escalation.
- `git ls-files --error-unmatch` runs as a check only; never alters git state.
- Filename validation (`startswith(".env")`, no `/`, no `..`) prevents writing arbitrary files into the repo.

## Concurrency

Same guarantee as today: last-writer-wins on the managed dir. Rebuilds take a process-level lock already (`REBUILD_LOCK`); the symlink dance happens inside it, so no two concurrent rebuilds on the same app collide.

## Verification

No test harness in the repo. Smoke matrix to run manually before merge:

1. **Migration** — app with a legacy `ENV_DIR/coloring.env`. After `fetch_app_status`, file is at `ENV_DIR/coloring/.env`, perms preserved, status panel still shows the same key count.
2. **Compose, single file** — app with only `.env` managed. `R` rebuilds; `<repo>/.env` is a symlink to the managed file; `docker inspect` shows expected env.
3. **Compose, multi-file** — app with `.env` and `.env.production` managed and a compose that references both. `R` succeeds; both symlinks present.
4. **Compose, tracked-file collision** — repo where `.env.example` is committed and the user (somehow) has a managed `.env.example`. `R` aborts with the clear error.
5. **Dockerfile mode, multi-file** — `docker inspect` shows multiple `--env-file` entries in the run command.
6. **`E` flow** — picker shows existing files + presets; picking a preset creates it 0600; editing surfaces the existing post-edit prompts.
7. **`I` flow** — importing from `<repo>/.env.production` writes into `ENV_DIR/<name>/.env.production`; ImportKeysScreen "replaces" markers reflect that file's existing keys.
8. **Idempotent rebuild** — second rebuild in a row produces no spurious symlink churn (same target, no relink).

## Rollout

Single PR. Migration is automatic and lazy. Existing canonical files keep working through migration. Users who only ever had one env file see no behavior change beyond the file moving one directory deeper. Users who use `I=import` see imports landing in per-file destinations instead of one canonical.

Version bump to 0.3.0 since this is a user-visible storage shape change (even if migrated transparently).
