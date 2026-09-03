## SubBud Fix & Improvement Plan

Every item below is marked `[x]` only when it is covered by a test in `tests/` that passes, or
by a manual run recorded in the item. Run the suite with `python -m pytest tests` against a
local Redis (it uses and flushes DB 15).

### Done

1. [x] **Require a project name for destructive/targeted operations**
   - `-p/--project` is required for every operation except `list` and `migrate`, so `delete`
     can no longer run without a target.
   - Where: `subbud/main.py::main`.
   - Verified: `subbud -p X -o add` with no `-f` and `subbud -o delete` both exit non-zero with
     an argparse error.

2. [x] **Stream file input instead of buffering it**
   - `add` reads the input file line by line and pushes fixed-size batches through `SADD`.
     `SADD`'s return value gives the new-domain count, so the project's existing set is never
     fetched. Memory is now O(batch), not O(file + set). The tqdm bar tracks bytes read.
   - Where: `Project.add_domains_from_file`, `DataStore.add_domains`.
   - Verified: `test_add_from_file_normalizes_and_dedups`, `test_add_from_file_raw_mode`,
     `test_add_from_file_rejects_missing_and_empty`.

3. [x] **Never materialize large Redis sets or key spaces**
   - `get_projects` uses `SCAN`, domain reads use `SSCAN`, and counts use `SCARD`. The last
     caller of `SMEMBERS` (the dedup step in `add`) is gone, and `DataStore.get_domains` was
     removed with it.
   - Verified: `test_add_count_and_delete`, `test_unrelated_keys_are_not_projects`.

4. [x] **Domain normalization and validation**
   - `normalize_domain` lowercases, strips scheme/userinfo/path/query/port/trailing dot and a
     leading `*.`, keeps the first whitespace token, and rejects anything that is not a valid
     multi-label hostname (labels 1–63 chars, no leading/trailing `-`, name ≤ 253 chars).
     Bare IPv4 addresses are accepted; single labels such as `localhost` are not. `--raw`
     disables it. Skipped lines are counted and reported.
   - Verified: `test_normalize_valid`, `test_normalize_invalid` (17 cases).

5. [x] **Namespaced Redis keys**
   - Keys are `subbud:<project>`. `get_projects` scans that prefix only, so unrelated keys in a
     shared DB are no longer listed as projects (which previously caused `WRONGTYPE` errors on
     `print`). `subbud -o migrate` renames pre-0.1 unprefixed **sets** into the namespace;
     `list` warns while any remain. Non-set keys are never migrated, and an existing project is
     never overwritten.
   - Verified: `test_keys_are_namespaced`, `test_unrelated_keys_are_not_projects`,
     `test_legacy_migration`, `test_legacy_migration_does_not_overwrite`.

6. [x] **CLI UX**
   - `--sort` for `print`/`save`, `--output` for `save`, `--password`/`REDIS_PASSWORD`.
   - Exit codes are meaningful: 0 on success, 1 on a missing project, empty result, unreachable
     Redis or file error. Previously every failure exited 0.
   - The connection check is a real `PING` on the configured client instead of a bare TCP
     connect, so wrong passwords and non-Redis listeners are reported instead of passing.
   - `except (FileNotFoundError, ValueError, Exception)` — which swallowed every bug in the
     program and printed it as a user error — is narrowed to the errors actually expected.
   - Verified: `test_save_domains_sorted`, `test_save_domains_empty_project`, plus a manual run
     of every operation against a live Redis.

7. [x] **Interactive TUI (`subbud-tui`)**
   - Textual UI with a project list, domain list, log panel and command bar.
   - Fixed while cleaning up: selecting a project or domain did nothing, because the handler
     read `Label.text`, which Textual does not define — the value is now carried on a
     `NamedItem`. `delete <project> confirm` printed the "type this to confirm" prompt and then
     deleted in the same breath, and mismatched on any project name containing a space.
     Single-key bindings (`q`/`r`/`c`) could never fire because the command Input holds focus;
     they are now `ctrl+q`/`ctrl+r`/`ctrl+l`. `edit`/`rm` silently succeeded on domains that
     were not in the set. The TUI did not load `.env`, so it ignored the config the CLI used.
   - Verified: scripted `App.run_test()` pilot session covering select → count → add → rm →
     edit → delete-with-confirm.

8. [x] **Packaging and repo hygiene**
   - `requirements.txt` was missing `tqdm` and `textual`, so the documented install could not
     produce a working `subbud-tui`; `setup.py` had no `install_requires` at all and shipped
     placeholder author/description metadata. Both fixed; `python_requires` raised to `>=3.8`
     (Textual's floor, and `>=3.6` was never true for this code).
   - Added `.gitignore`; `build/` and `subbud.egg-info/` are untracked build artifacts and
     should not be committed.

9. [x] **Wildcard and out-of-scope filtering**
   - `--scope`/`--exclude` take repeatable patterns and `--scope-file`/`--exclude-file` take
     files of them, applied at `add` time. `example.com` covers the apex and its subdomains,
     `*.example.com` covers subdomains only; excludes always beat includes; out-of-scope
     domains are counted and reported like invalid lines. An unparseable pattern is a fatal
     argument error rather than a silently ignored line, so a typo in a scope file cannot
     quietly change what gets stored.
   - Where: `subbud/domains.py` (`ScopePattern`, `ScopeFilter`), wired into
     `Project.add_domains_from_file`.
   - Verified: 8 scope tests plus `test_add_from_file_applies_scope`,
     `test_add_from_file_all_out_of_scope`.

10. [x] **Per-domain metadata**
    - `add` records the date a domain was first seen and the source it came from
      (`--source` overrides the default of the input file name). Writes are first-write-wins,
      so re-scanning never rewrites history. `print --meta` and `save --meta` emit TSV, with
      `-` for domains that predate the feature — no migration needed.
    - On Redis this is a companion hash `subbud:<project>:meta`, excluded from `get_projects`
      and deleted with the project; on SQLite the fields are columns on the same row. Removing
      a domain removes its metadata on both. Project names may not end in `:meta`, which is
      enforced rather than left to collide.
    - Verified: 6 metadata store tests, `test_metadata_key_is_not_a_project`, plus
      `test_add_from_file_records_metadata`, `test_add_from_file_source_override`,
      `test_save_domains_with_meta`, `test_print_domains_with_meta`.

11. [x] **Redis-free fallback store**
    - `subbud/stores.py` holds both backends behind one interface: `DataStore` (Redis, default)
      and `SqliteStore` (`--store sqlite` / `SUBBUD_STORE`, default file `~/.subbud/subbud.db`,
      stdlib `sqlite3` only). `open_store()` picks between them for the CLI and the TUI.
    - There is deliberately no automatic Redis→SQLite fallback: silently switching backends
      would split a project's domains across two stores. `-o migrate` is rejected as an
      argument error on SQLite instead of silently doing nothing.
    - Verified: the `store` fixture is parametrized over both backends, so every store and
      `Project` test runs twice; Redis-only tests skip cleanly on the SQLite pass.

### Notes

- `subbud/main.py` was split: `subbud/domains.py` (normalization, scope) and
  `subbud/stores.py` (backends). `main.py` re-exports `DataStore`, `normalize_domain`,
  `project_key` and `Project`, so existing `from subbud.main import ...` code still works.

### Open

Nothing tracked. Ideas that were considered and deliberately left out: persisting a scope per
project (scope is per-`add` today), scope filtering in the TUI, and resolution/liveness status
per domain — the last one wants a resolver and a refresh loop, which is a different tool.
