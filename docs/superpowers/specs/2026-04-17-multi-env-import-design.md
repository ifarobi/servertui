# Multi-`.env` Import for Apps

**Status:** approved
**Date:** 2026-04-17
**Builds on:** PR #2 (first-edit seeding of `ENV_DIR/<name>.env`)

## Problem

ServerTUI stores each app's canonical env at `~/.config/servertui/env/<name>.env`
(outside the repo, 0600, injected at rebuild via `docker --env-file`).
PR #2 fixed the original "blank editor" bug by seeding that file from
`<repo>/.env` on first `E` press.

Some apps carry multiple env files in their repo
(`.env`, `.env.local`, `.env.staging`, `.env.production`).
The auto-seed covers only `<repo>/.env`, and there is no way to:

- pick a non-default source file (e.g. import from `.env.staging`),
- import a subset of keys rather than the whole file,
- re-import after the canonical already exists.

## Non-goals

- Cross-file discrepancy detection (considered and dropped as noise —
  prod/staging are expected to diverge).
- Editing env files inside the repo. Repo `.env*` files are read-only to us.
- Two-way sync between canonical and repo files.

## User-facing behavior

### New action: `I` = Import env (Apps tab)

1. Press `I` → `SelectorScreen` listing configured apps
   (reuses the existing app-picker pattern from `E` / `R` / `L`).
2. Scan the selected app's `repo_path` for the fixed candidate set:
   `.env`, `.env.local`, `.env.development`, `.env.staging`, `.env.production`.
   Keep only the files that exist.
3. If 0 candidates → notify `"no .env files found in <repo_path>"` and end.
4. Otherwise show a `SelectorScreen` of the discovered candidates.
5. User picks one source → parse it.
6. Show `ImportKeysScreen` (new modal) with a per-key checkbox list.
   Each row: `KEY=value  [new]` or `KEY=value  [replaces]`
   depending on whether the key already exists in the canonical file.
7. User toggles keys and submits.
   - 0 selected → notify `"import cancelled — no keys selected"` and end.
8. Merge selected keys into `ENV_DIR/<name>.env`.
9. Notify `"Imported N keys from <source>"`.
10. Reuse the existing "restart container?" prompt from the `E` flow.

### Changes to the existing `E` flow

Only one behavior change, and only when the canonical file does **not** exist yet:

- If the repo has **exactly one** candidate → auto-seed it (unchanged, matches PR #2).
- If the repo has **multiple** candidates → write the template header, but
  add a breadcrumb:
  `# Multiple .env* files detected. Press I on the Apps tab to import.`
- If the repo has **no** candidates → template, unchanged.

Existing canonical files are never touched by this change. Users who already
ran PR #2's auto-seed see no regression.

### Footer / keybindings

- Apps panel hint gains `I=import env`.
- `CLAUDE.md` keybindings line updates the apps cluster:
  `R/E/I/L=rebuild/edit env/import env/logs`.

## Architecture

No new files. Two layers:

### `core.py` — pure helpers (testable without Textual)

**`parse_env_file(path: Path) -> list[tuple[str, str]]`**

Ordered list of `(key, value)`. Parses line by line:

- Strips leading whitespace; ignores blank lines and lines starting with `#`.
- Accepts `KEY=value` and `export KEY=value` (the `export` prefix is stripped).
- Value handling:
  - Leading/trailing whitespace trimmed.
  - If wrapped in matching `"..."` or `'...'`, quotes are stripped.
    Double-quoted values support `\n`, `\t`, `\\`, `\"` escapes; single-quoted
    values are taken literally (matches `docker --env-file` documentation).
  - Inline `#` starts a comment only in unquoted values.
- Malformed lines (no `=`, bad quoting) are logged to `stderr` and skipped;
  parsing never raises.
- Duplicate keys in source: last wins.

**`merge_env_keys(canonical_path: Path, updates: list[tuple[str, str]]) -> int`**

Rewrites the canonical file with the updates applied. Returns the number of
keys written (i.e., `len(updates)` on success).

Algorithm:

1. If `canonical_path` does not exist, create it atomically via
   `os.open(..., O_WRONLY|O_CREAT|O_EXCL, 0o600)` seeded with the same template
   header PR #2 writes. Reuses the TOCTOU-safe pattern already in the codebase.
2. Read existing file as a list of lines (preserving exact bytes).
3. Walk lines with `parse_env_file`'s key-matching rules to build
   `key -> list[line_index]`.
4. Partition `updates` into:
   - `in_place`: key already present in canonical.
   - `appended`: key absent.
5. Apply `in_place` updates:
   - For each, rewrite the **last** matching line as `KEY=<quoted_value>\n`.
   - Delete any earlier occurrences of the same key (opportunistic dedup).
6. If `appended` is non-empty:
   - Ensure file ends with `\n`.
   - Append blank line, then `# imported <YYYY-MM-DD> from <source_filename>`
     (e.g. `.env.staging`), then one `KEY=<quoted_value>` line per appended
     update.
7. Write atomically: open `<path>.tmp` via `O_WRONLY|O_CREAT|O_EXCL` mode
   `0o600`, write full contents, `os.replace(<path>.tmp, <path>)`.
8. Return count.

Value quoting on write:

- If value is empty, or contains only `[A-Za-z0-9_./:@+\-]` → emit bare.
- Otherwise → wrap in double quotes, escape inner `"` and `\`.

### `app.py` — UI and orchestration

**`ImportKeysScreen(ModalScreen[list[tuple[str, str]] | None])`**

- Composed of a title, a Textual `SelectionList[int]` (index into the parsed
  key list), and two buttons: Import / Cancel.
- Each row label renders as `{KEY}={value_preview}  [{new|replaces}]`
  where `value_preview` truncates at ~40 chars and shows `…` for the rest.
- Rows for `[replaces]` are styled with a subtle accent color (reuse existing
  Rich markup palette — `dim yellow` or similar).
- Dismiss result: `list[(key, value)]` of selected keys, or `None` on cancel.

**`action_app_import_env(self) -> None`**

Bound to `Binding("I", "app_import_env", "Import env")`. Orchestrates the
three-screen sequence using the existing `push_screen(..., callback)` idiom
(same shape as `action_app_edit_env`). Flow:

1. Push app selector.
2. On app choice, scan `repo_path` for candidates.
3. Push source selector (reuse `SelectorScreen`).
4. On source choice, call `parse_env_file(source)`. If empty, notify and end.
5. Read canonical to determine `canonical_keys: set[str]`. Pass
   `(parsed_keys, canonical_keys, source_filename)` into `ImportKeysScreen`.
6. On submit, call `merge_env_keys(canonical, selected)`.
7. Notify; offer restart prompt (extract the `maybe_restart` inner
   function from `action_app_edit_env` into a shared method if
   practical; otherwise duplicate the 10 lines).

**Minor change to `edit_env_file`**

When creating a new canonical file and the repo has **multiple** candidates,
branch to template-with-breadcrumb instead of importing `.env` by default.
Single-candidate path unchanged.

## Error handling

All errors surface via `notify(..., severity="error")`. No exceptions leak to
Textual's event loop.

| Case | Behavior |
|---|---|
| `repo_path` missing | `notify("repo not cloned: run Rebuild first")` |
| No candidate files | `notify("no .env files found in <repo_path>")` |
| Source read fails | `notify(str(e))`, canonical untouched |
| Source parses to 0 keys | `notify("no keys to import from <source>")` |
| User submits 0 keys | `notify("import cancelled — no keys selected")` |
| Canonical perms drift from 0600 | Same refusal as `edit_env_file` — notify and end |
| Canonical write fails | Atomic tmp+replace keeps old file intact; notify error |
| User ESCs any selector | End cleanly, no writes |

## Security

- Canonical file is always created or replaced with mode `0o600` via `O_EXCL`
  on open. Matches the TOCTOU-safe pattern from PR #2 and the earlier review fix.
- `os.replace` preserves the mode of the source tmp file, so the final canonical
  stays 0600.
- Repo `.env*` files are opened read-only; never written.
- No canonical path outside `ENV_DIR` is ever touched.

## Concurrency

Concurrent writers (MCP server, a second TUI instance) can race the canonical.
Last writer wins — same guarantee as the existing `E` flow. Frequency does not
justify introducing a lock.

## Verification

Project has no test harness (per `CLAUDE.md`), so verification mirrors PR #2:

**Smoke script** (run from a worktree without full deps, shimming `docker`
and `textual`) covers:

- `parse_env_file`: bare/quoted/escaped values, `export` prefix, `#` comments,
  malformed lines skipped, duplicate keys last-wins.
- `merge_env_keys`:
  - Creates canonical when absent (0600, seeded with template).
  - In-place replace preserves unrelated lines and comments.
  - Dedup: canonical with `KEY=a` then `KEY=b` collapses to one after replace.
  - Appended keys get a dated `# imported …` marker.
  - Atomic write: crashing mid-write (simulated) leaves canonical intact.
  - 0-update call is a no-op.

**Manual on server:**

1. App with only `.env` → `I` flow works, collision indicators correct.
2. App with `.env` + `.env.production` → source selector appears; imports cleanly.
3. `E` on an app with multiple candidates and no canonical → template with
   breadcrumb, not auto-import.
4. `E` on an app with only `.env` and no canonical → auto-seeds (no regression).

## Rollout

Single PR. No migration. Existing canonical files untouched by any code path.
