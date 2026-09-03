# SubBud - Bug Bounty Subdomain Management Tool 🐞

**SubBud** is a command-line tool for managing subdomains collected from various sources
(amass, subfinder, crt.sh, …) for bug bounty hunters and security researchers. It stores each
project's subdomains in a set, so merging results from many tools is deduplicated for free,
and it records when each domain was first seen and which input it came from. It ships with a
CLI (`subbud`) and an interactive TUI (`subbud-tui`). 🎯

## Table of Contents 📋

- [Installation](#installation)
- [Configuration](#configuration)
- [Usage](#usage)
  - [Adding Subdomains](#adding-subdomains)
  - [Printing Subdomains](#printing-subdomains)
  - [Listing Projects](#listing-projects)
  - [Deleting Projects](#deleting-projects)
  - [Saving Merged Subdomains](#saving-merged-subdomains)
  - [Migrating Old Projects](#migrating-old-projects)
- [Domain Normalization](#normalization)
- [Scope Filtering](#scope)
- [Domain Metadata](#metadata)
- [Storage Backends](#backends)
- [Interactive TUI](#tui)
- [Requirements](#requirements)
- [Development](#development)
- [Contributing](#contributing)

# Installation 🚀 <a name="installation"></a>

SubBud is available on PyPI:

```bash
pip install subbud
```

Or from a checkout:

```bash
pip install -e .
```

# Configuration ⚙️ <a name="configuration"></a>

Settings come from the environment (a `.env` file in the working directory is loaded
automatically) and can be overridden per command:

| Variable             | Flag            | Default             |
| -------------------- | --------------- | ------------------- |
| `SUBBUD_STORE`       | `--store`       | `redis`             |
| `SUBBUD_SQLITE_PATH` | `--sqlite-path` | `~/.subbud/subbud.db` |
| `REDIS_HOST`         | `--host`        | `localhost`         |
| `REDIS_PORT`         | `--port`        | `6379`              |
| `REDIS_DB`           | `--db`          | `0`                 |
| `REDIS_PASSWORD`     | `--password`    | *(none)*            |
| `REDIS_SSL`          | `--ssl`         | `false`             |

With the default Redis backend, all keys are stored as `subbud:<project>`, so SubBud never
lists or deletes keys belonging to other applications sharing the same database.

# Usage 🛠️ <a name="usage"></a>

## Adding Subdomains ➕<a name="adding-subdomains"></a>

To add subdomains to a project, use the `-o add` operation:

```bash
subbud -p project_name -o add -f subdomainsfromamass.txt
```

- `-p project_name`: The name of your project.
- `-o add`: Specifies the operation to add subdomains.
- `-f subdomains.txt`: The file containing subdomains to add.
- `--source LABEL` (optional): what to record as the origin of these domains
  (default: the input file name).
- `--raw` (optional): store lines verbatim, skipping normalization and validation.
- `--scope` / `--exclude` (optional): see [Scope Filtering](#scope).

The file is streamed, so files of any size are safe to add. SubBud reports how many domains
were new, how many were duplicates, how many lines it ignored as invalid, and how many it
dropped as out of scope.

## Printing Subdomains 🖨️ <a name="printing-subdomains"></a>

To print merged/unique subdomains for a project, use the `-o print` operation:

```bash
subbud -p project_name -o print [--sort] [--meta]
```

Sets are unordered; pass `--sort` for alphabetical output. `--meta` adds first-seen and source
columns.

## Listing Projects 📃<a name="listing-projects"></a>

```bash
subbud -o list
```

## Deleting Projects ❌<a name="deleting-projects"></a>

To delete a project, its subdomains and their metadata:

```bash
subbud -p project_name -o delete
```

This is immediate and irreversible. `-p/--project` is required for every operation except
`list` and `migrate`.

## Saving Merged Subdomains 💾<a name="saving-merged-subdomains"></a>

To save the merged subdomains to a text file:

```bash
subbud -p project_name -o save [--sort] [--meta] [--output path.txt]
```

Without `--output`, the file is written to the current directory as `<YYYY-MM-DD>_<project>.txt`.
An existing file at that path is overwritten.

## Migrating Old Projects 📦<a name="migrating-old-projects"></a>

SubBud ≤ 0.0.2 stored projects as bare Redis keys. Since 0.1.0 keys are prefixed with
`subbud:`. `subbud -o list` warns when old keys are present; move them over with:

```bash
subbud -o migrate
```

Migration renames each unprefixed Redis **set** to `subbud:<name>`. Keys of other types are
never touched, and a rename is skipped if a project of that name already exists. The operation
applies to the Redis backend only.

# Domain Normalization 🧹 <a name="normalization"></a>

Unless `--raw` is passed, every input line is normalized before storage:

- blank lines and `#` comments are ignored;
- only the first whitespace-separated token is kept (tool output often appends annotations);
- the value is lowercased, and a URL scheme, userinfo, path/query/fragment, trailing port and
  trailing root dot are stripped;
- a leading wildcard label is dropped (`*.example.com` → `example.com`);
- anything that is not a valid multi-label hostname is skipped and counted. Bare IPv4
  addresses are accepted.

So `HTTPS://User@C.Example.com:8443/path?q=1` and `c.example.com.` both store as
`c.example.com`.

# Scope Filtering 🚫 <a name="scope"></a>

Keep out-of-scope domains out of a project at `add` time instead of filtering them downstream:

```bash
subbud -p acme -o add -f all.txt --scope-file program-scope.txt --exclude dev.example.com
```

| Flag             | Meaning                                                        |
| ---------------- | -------------------------------------------------------------- |
| `--scope`        | Only store domains matching this pattern (repeatable)           |
| `--exclude`      | Never store domains matching this pattern (repeatable)          |
| `--scope-file`   | File of `--scope` patterns, one per line (repeatable)           |
| `--exclude-file` | File of `--exclude` patterns, one per line (repeatable)         |

Pattern rules:

- `example.com` matches the apex **and** every subdomain of it.
- `*.example.com` matches subdomains only, not the apex.
- Patterns are normalized like domains, and pattern files ignore blanks and `#` comments.
- A domain is stored when it matches at least one `--scope` pattern (or none were given) and
  matches no `--exclude` pattern. **Excludes always win.**
- An unparseable pattern is a fatal argument error, so a typo in a scope file never silently
  widens or narrows what gets stored.

# Domain Metadata 🕒 <a name="metadata"></a>

Every `add` records the date a domain was **first** seen and the input it came from. Later
sightings never overwrite that first record, so the history survives repeated scans:

```bash
$ subbud -p acme -o print --sort --meta
✅ 2 domains found for the 'acme' project:
domain	first_seen	source
api.example.com	2026-09-03	amass.txt
www.example.com	2026-08-11	crtsh
```

Output is tab-separated. `--source LABEL` overrides the recorded source. Domains added before
this feature existed, or added with `--raw`, render as `-`; nothing needs migrating. On Redis
the metadata lives in a companion hash, `subbud:<project>:meta`, which is deleted alongside
the project and is never listed as one — project names may therefore not end in `:meta`.

# Storage Backends 🗄 <a name="backends"></a>

| Backend  | Select with      | Notes                                                     |
| -------- | ---------------- | --------------------------------------------------------- |
| `redis`  | *(default)*      | Needs a running Redis server; supports `-o migrate`         |
| `sqlite` | `--store sqlite` | Single local file, no server; uses Python's stdlib `sqlite3` |

```bash
subbud --store sqlite -p acme -o add -f domains.txt
SUBBUD_STORE=sqlite subbud -o list          # or set it once in .env
```

Both backends implement the same operations and are covered by the same tests. There is
deliberately **no** automatic fallback from Redis to SQLite: silently switching backends would
split a project's domains across two stores. Pick one explicitly.

# Interactive TUI 🖥️ <a name="tui"></a>

```bash
subbud-tui
```

A full-screen Textual interface: project list on the left, domains and a log panel on the
right, and a command bar at the bottom. It reads the same environment variables as the CLI,
including `SUBBUD_STORE`. Type `help` for the command list:

| Command                  | Description                                          |
| ------------------------ | ---------------------------------------------------- |
| `list`                   | Refresh the project list                              |
| `use <project>`          | Set the current project (alias: `new`)                |
| `add [<project>] <file>` | Add domains from a file                               |
| `print [<project>]`      | Print domains into the log panel                      |
| `count [<project>]`      | Show the domain count                                 |
| `save [<project>]`       | Save domains to `<date>_<project>.txt`                |
| `delete <project>`       | Delete a project (asks you to repeat with `confirm`)  |
| `meta [<domain>]`        | First-seen date and source of a domain                |
| `edit [<old>] <new>`     | Rename the selected or given domain                   |
| `rm [<domain>]`          | Remove the selected or given domain                   |
| `store`                  | Show the current backend details                      |

Keys: `ctrl+q` quit, `ctrl+r` refresh projects, `ctrl+l` clear log, `tab` move focus into the
lists (then arrow keys + `enter` to select). Shortcuts are ctrl-based because the command bar
keeps keyboard focus. The domain list shows the first 200 domains of a project; `print` and
`save` always cover the whole set. Scope filtering is CLI-only.

## Requirements ⚙️<a name="requirements"></a>

- Python 3.8+
- A running Redis server for the default backend (not needed with `--store sqlite`) 🛠️
- `redis`, `python-dotenv`, `tqdm`, `textual` (installed automatically)

## Development 🧪<a name="development"></a>

```bash
pip install -r requirements.txt
pip install -e .
python -m pytest tests
```

Store tests run against both backends. The SQLite ones use a temporary file; the Redis ones
use Redis DB 15 and flush it, and are skipped automatically when no Redis server is reachable.

## Contributing 🤝<a name="contributing"></a>

Contributions to SubBud are welcome! If you have any suggestions, improvements, or bug fixes,
please open an issue or create a pull request!

Inspired by [@jhaddix](https://github.com/jhaddix) ❤️ Sir !
