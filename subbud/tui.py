import os
from datetime import datetime
from typing import List, Optional

from dotenv import load_dotenv
from textual.app import App, ComposeResult
from textual.containers import Horizontal, Vertical
from textual.widgets import Footer, Header, Input, Label, ListItem, ListView, Static

from .domains import normalize_domain
from .stores import DEFAULT_SQLITE_PATH, open_store

load_dotenv()


class NamedItem(ListItem):
    """
    A ListItem that remembers the value it displays.

    Textual widget ids have strict syntax rules that domain and project names
    routinely violate, and `Label` exposes no readable `.text`, so the value is
    kept on the item itself.
    """

    def __init__(self, value: str) -> None:
        super().__init__(Label(value))
        self.value = value


class SubBudTUI(App):
    """
    Full-screen TUI for SubBud, built with Textual.

    - Left: project list
    - Right: domains of the selected project, plus a log panel
    - Bottom: command bar (type `help` for the command list)

    The command bar keeps focus, so shortcuts are ctrl-based; press `tab` to
    move focus into the lists and use the arrow keys there.
    """

    CSS = """
    Screen {
        layout: vertical;
        background: $surface;
    }

    #body {
        layout: horizontal;
    }

    #projects_panel {
        width: 30%;
        border: heavy $accent;
    }

    #projects_title {
        padding: 1 1;
        text-style: bold;
        background: $accent 20%;
    }

    #projects {
        height: 1fr;
    }

    #detail_panel {
        border: heavy $accent;
    }

    #detail_title {
        padding: 1 1;
        text-style: bold;
        background: $boost;
    }

    #domains {
        height: 1fr;
    }

    #log {
        height: 6;
        border-top: solid $accent;
    }

    #command_bar {
        height: 3;
        border-top: solid $accent;
    }

    #command_label {
        width: 12;
        content-align: right middle;
    }

    #command_input {
        width: 1fr;
    }
    """

    # Single-letter bindings would be swallowed by the command Input, which
    # holds focus by default, so every shortcut is ctrl-based.
    BINDINGS = [
        ("ctrl+q", "quit", "Quit"),
        ("ctrl+r", "refresh_projects", "Refresh projects"),
        ("ctrl+l", "clear_log", "Clear log"),
    ]

    # Domains rendered in the side list for a project; the full set is always
    # available via `print`, `save` and `count`.
    MAX_DOMAINS_SHOWN = 200

    def __init__(self, **kwargs) -> None:
        super().__init__(**kwargs)
        self.datastore = None
        self.current_project: Optional[str] = None
        self.current_domain: Optional[str] = None
        self._log_lines: List[str] = []

    def compose(self) -> ComposeResult:
        yield Header(show_clock=True)
        with Horizontal(id="body"):
            with Vertical(id="projects_panel"):
                yield Label("Projects", id="projects_title")
                yield ListView(id="projects")
            with Vertical(id="detail_panel"):
                yield Label("No project selected", id="detail_title")
                yield ListView(id="domains")
                # Simple scrolling log implemented with a Static widget
                yield Static(id="log")
        with Horizontal(id="command_bar"):
            yield Static("Command:", id="command_label")
            yield Input(
                placeholder="help | list | use <project> | add <project> <file> | print | save | delete",
                id="command_input",
            )
        yield Footer()

    async def on_mount(self) -> None:
        self.log_widget = self.query_one("#log", Static)
        self.projects_list = self.query_one("#projects", ListView)
        self.domains_list = self.query_one("#domains", ListView)
        self.detail_title = self.query_one("#detail_title", Label)
        self.command_input = self.query_one("#command_input", Input)

        self.command_input.focus()
        await self.connect_store()
        await self.action_refresh_projects()

    # --- Logging helpers -------------------------------------------------

    def log_write(self, message: str) -> None:
        """Append a line to the log panel."""
        self._log_lines.append(message)
        # Keep only the last N lines to avoid unbounded growth
        max_lines = 500
        if len(self._log_lines) > max_lines:
            self._log_lines = self._log_lines[-max_lines:]
        self.log_widget.update("\n".join(self._log_lines))

    def log_clear(self) -> None:
        """Clear the log panel."""
        self._log_lines.clear()
        self.log_widget.update("")

    async def connect_store(self) -> None:
        """
        Open the backend named by SUBBUD_STORE ('redis' or 'sqlite'), using the
        same environment variables as the CLI.
        """
        backend = os.getenv("SUBBUD_STORE", "redis")
        try:
            self.datastore = open_store(
                backend=backend,
                host=os.getenv("REDIS_HOST", "localhost"),
                port=int(os.getenv("REDIS_PORT", "6379")),
                db=int(os.getenv("REDIS_DB", "0")),
                password=os.getenv("REDIS_PASSWORD"),
                use_ssl=os.getenv("REDIS_SSL", "false").lower() in ["true", "1"],
                sqlite_path=os.getenv("SUBBUD_SQLITE_PATH", DEFAULT_SQLITE_PATH),
            )
            self.log_write(f"✅ Connected: {self.datastore.describe()}")
        except (ConnectionError, ValueError) as e:
            self.log_write(str(e))
            self.log_write(
                "❌ Unable to connect. Set SUBBUD_STORE / REDIS_HOST / REDIS_PORT / REDIS_DB / "
                "REDIS_PASSWORD / REDIS_SSL and restart `subbud-tui`."
            )

    async def action_refresh_projects(self) -> None:
        if not self.datastore:
            return
        self.projects_list.clear()
        projects = self.datastore.get_projects()
        for name in projects:
            self.projects_list.append(NamedItem(name))
        self.log_write(f"📋 Loaded {len(projects)} projects.")

        legacy = self.datastore.find_legacy_projects()
        if legacy:
            self.log_write(
                f"⚠️ {len(legacy)} project(s) still use pre-0.1 unprefixed keys. "
                "Run `subbud -o migrate` from the shell to move them."
            )

    async def action_clear_log(self) -> None:
        self.log_clear()

    async def on_list_view_selected(self, event: ListView.Selected) -> None:
        """Track the selected project or domain."""
        item = event.item
        if not isinstance(item, NamedItem):
            return

        if event.list_view.id == "projects":
            self.current_project = item.value
            self.current_domain = None
            self.log_write(f"🐞 Selected project: {item.value}")
            await self.show_project_details(item.value)
        elif event.list_view.id == "domains":
            self.current_domain = item.value
            self.log_write(f"✏️ Selected domain: {item.value}")

    async def show_project_details(self, project: str) -> None:
        """Load and display basic details and domains for a project."""
        if not self.datastore:
            return

        domain_count = self.datastore.get_domain_count(project)
        self.detail_title.update(f"Project: {project} ({domain_count} domains)")

        self.domains_list.clear()
        if domain_count == 0:
            return

        shown = 0
        for domain in self.datastore.iter_domains(project):
            if shown >= self.MAX_DOMAINS_SHOWN:
                break
            self.domains_list.append(NamedItem(domain))
            shown += 1

        if domain_count > self.MAX_DOMAINS_SHOWN:
            self.log_write(
                f"ℹ️ Showing first {self.MAX_DOMAINS_SHOWN} domains out of {domain_count} for '{project}'."
            )

    async def on_input_submitted(self, event: Input.Submitted) -> None:
        text = event.value.strip()
        self.command_input.value = ""
        if not text:
            return
        await self.handle_command(text)

    async def handle_command(self, text: str) -> None:
        if not self.datastore:
            self.log_write("❌ Not connected to a storage backend.")
            return

        parts: List[str] = text.split()
        cmd = parts[0].lower()
        args = parts[1:]

        handlers = {
            "use": self.cmd_use,
            "new": self.cmd_use,
            "add": self.cmd_add,
            "print": self.cmd_print,
            "save": self.cmd_save,
            "delete": self.cmd_delete,
            "count": self.cmd_count,
            "edit": self.cmd_edit,
            "rm": self.cmd_rm,
            "meta": self.cmd_meta,
        }

        if cmd in ("help", "?"):
            self.show_help()
        elif cmd == "list":
            await self.action_refresh_projects()
        elif cmd in ("store", "redis"):
            self.show_store_info()
        elif cmd in handlers:
            await handlers[cmd](args)
        else:
            self.log_write(f"❌ Unknown command: {cmd}. Type `help` for options.")

    def show_help(self) -> None:
        self.log_write("🛈 Commands:")
        self.log_write("  list                         - Refresh project list")
        self.log_write("  use <project>                - Set current project (alias: new)")
        self.log_write("  add <project> <file>         - Add domains from file to project")
        self.log_write("  add <file>                   - Add domains to current project")
        self.log_write("  print [<project>]            - Print domains for a project")
        self.log_write("  count [<project>]            - Show domain count for a project")
        self.log_write("  save [<project>]             - Save domains for a project to file")
        self.log_write("  delete [<project>]           - Delete a project (asks to confirm)")
        self.log_write("  store                        - Show storage backend details")
        self.log_write("  meta [<domain>]              - First-seen date and source of a domain")
        self.log_write("  edit [<old>] <new>           - Rename selected or given domain")
        self.log_write("  rm [<domain>]                - Remove selected or given domain")
        self.log_write("Keys: ctrl+q=quit, ctrl+r=refresh, ctrl+l=clear log, tab=move focus")

    async def cmd_use(self, args: List[str]) -> None:
        if not args:
            self.log_write("Usage: use <project>")
            return
        name = args[0]
        self.current_project = name
        self.current_domain = None
        self.log_write(f"🐞 Current project: {name}")
        await self.show_project_details(name)

    def _resolve_project_arg(self, args: List[str]) -> Optional[str]:
        if args:
            return args[0]
        return self.current_project

    async def cmd_add(self, args: List[str]) -> None:
        if not args:
            self.log_write("Usage: add <project> <file> OR add <file> (with current project set)")
            return

        if len(args) == 1:
            project = self.current_project
            filename = args[0]
        else:
            project = args[0]
            filename = " ".join(args[1:])

        if not project:
            self.log_write("❌ No project specified or selected. Use `use <project>` first.")
            return

        filename = os.path.expanduser(filename)
        if not os.path.exists(filename):
            self.log_write(f"❌ File does not exist: {filename}")
            return

        # Same normalization, streaming and dedup rules as the CLI, but
        # counted here so the results land in the log panel.
        total = added = skipped = 0
        batch: List[str] = []
        first_seen = datetime.now().strftime("%Y-%m-%d")
        source = os.path.basename(filename)

        def flush() -> int:
            if not batch:
                return 0
            new = self.datastore.add_domains(project, batch)
            self.datastore.record_metadata(project, batch, first_seen, source)
            batch.clear()
            return new

        with open(filename, "r", errors="replace") as handle:
            for line in handle:
                domain = normalize_domain(line)
                if domain is None:
                    if line.strip() and not line.strip().startswith("#"):
                        skipped += 1
                    continue
                total += 1
                batch.append(domain)
                if len(batch) >= 1000:
                    added += flush()
            added += flush()

        if total == 0:
            self.log_write(f"❌ No usable domains found in '{filename}'.")
            return

        self.log_write(f"✅ Added {added} new domains to '{project}'.")
        self.log_write(f"🔍 Duplicates skipped: {total - added}")
        self.log_write(f"🔄 Percentage of new domains: {added / total * 100:.2f}%")
        if skipped:
            self.log_write(f"⚠️ Ignored {skipped} lines that are not valid hostnames.")

        if project == self.current_project:
            await self.show_project_details(project)

    async def cmd_print(self, args: List[str]) -> None:
        project = self._resolve_project_arg(args)
        if not project:
            self.log_write("Usage: print <project> OR print (with current project set)")
            return

        domain_count = self.datastore.get_domain_count(project)
        if domain_count == 0:
            self.log_write(f"❌ No domains found for project '{project}'.")
            return

        self.log_write(f"✅ {domain_count} domains for '{project}':")
        for domain in self.datastore.iter_domains(project):
            self.log_write(domain)

    async def cmd_count(self, args: List[str]) -> None:
        project = self._resolve_project_arg(args)
        if not project:
            self.log_write("Usage: count <project> OR count (with current project set)")
            return
        domain_count = self.datastore.get_domain_count(project)
        self.log_write(f"📊 Project '{project}' has {domain_count} domains.")

    async def cmd_save(self, args: List[str]) -> None:
        project = self._resolve_project_arg(args)
        if not project:
            self.log_write("Usage: save <project> OR save (with current project set)")
            return

        domain_count = self.datastore.get_domain_count(project)
        if domain_count == 0:
            self.log_write(f"❌ No domains found for project '{project}'.")
            return

        filename = f"{datetime.now().strftime('%Y-%m-%d')}_{project}.txt"
        with open(filename, "w") as file:
            for domain in self.datastore.iter_domains(project):
                file.write(domain + "\n")

        self.log_write(f"✅ Saved {domain_count} domains for '{project}' to '{filename}'")

    async def cmd_delete(self, args: List[str]) -> None:
        """
        Delete a project. Deletion is irreversible, so it takes two steps:
        `delete <project>` prints the exact confirmation line to type back.
        """
        confirmed = bool(args) and args[-1].lower() == "confirm"
        name_args = args[:-1] if confirmed else args
        project = " ".join(name_args) if name_args else self.current_project

        if not project:
            self.log_write("Usage: delete <project> OR delete (with current project set)")
            return

        if not confirmed:
            self.log_write(f"⚠️ To confirm deletion of '{project}', type: delete {project} confirm")
            return

        if self.datastore.delete_project(project):
            self.log_write(f"✅ Project '{project}' deleted.")
            if self.current_project == project:
                self.current_project = None
                self.current_domain = None
                self.domains_list.clear()
                self.detail_title.update("No project selected")
            await self.action_refresh_projects()
        else:
            self.log_write(f"❌ Project '{project}' not found.")

    async def cmd_edit(self, args: List[str]) -> None:
        """
        Rename a domain in the current project.

        Usage:
          edit <new-domain>              # renames the selected domain
          edit <old-domain> <new-domain>
        """
        if not self.current_project:
            self.log_write("❌ No project selected. Use `use <project>` first.")
            return

        if not args:
            self.log_write("Usage: edit <new-domain> OR edit <old-domain> <new-domain>")
            return

        if len(args) == 1:
            old, new = self.current_domain, args[0]
        else:
            old, new = args[0], args[1]

        if not old:
            self.log_write("❌ No domain selected or specified to edit.")
            return

        new_normalized = normalize_domain(new)
        if new_normalized is None:
            self.log_write(f"❌ '{new}' is not a valid hostname.")
            return

        # Remove first and check the result, so a typo in <old> is reported
        # instead of silently adding an unrelated domain.
        if not self.datastore.remove_domains(self.current_project, [old]):
            self.log_write(f"❌ Domain '{old}' is not in '{self.current_project}'.")
            return
        self.datastore.add_domains(self.current_project, [new_normalized])

        self.log_write(f"✏️ Renamed '{old}' -> '{new_normalized}' in '{self.current_project}'.")
        self.current_domain = new_normalized
        await self.show_project_details(self.current_project)

    async def cmd_rm(self, args: List[str]) -> None:
        """
        Remove a domain from the current project.

        Usage:
          rm            # removes the selected domain
          rm <domain>
        """
        if not self.current_project:
            self.log_write("❌ No project selected. Use `use <project>` first.")
            return

        target = args[0] if args else self.current_domain
        if not target:
            self.log_write("Usage: rm <domain> OR rm (with a domain selected).")
            return

        if not self.datastore.remove_domains(self.current_project, [target]):
            self.log_write(f"❌ Domain '{target}' is not in '{self.current_project}'.")
            return

        self.log_write(f"🗑 Removed domain '{target}' from '{self.current_project}'.")
        if self.current_domain == target:
            self.current_domain = None

        await self.show_project_details(self.current_project)

    async def cmd_meta(self, args: List[str]) -> None:
        """Show first-seen date and source for the selected or given domain."""
        if not self.current_project:
            self.log_write("❌ No project selected. Use `use <project>` first.")
            return

        target = args[0] if args else self.current_domain
        if not target:
            self.log_write("Usage: meta <domain> OR meta (with a domain selected).")
            return

        record = self.datastore.get_metadata(self.current_project, target)
        if not record:
            self.log_write(f"ℹ️ No metadata recorded for '{target}'.")
            return
        self.log_write(
            f"🕒 {target}: first seen {record['first_seen']} via {record['source'] or '-'}"
        )

    def show_store_info(self) -> None:
        if not self.datastore:
            self.log_write("❌ Not connected to a storage backend.")
            return
        self.log_write(f"🗄 {self.datastore.describe()}")


def main() -> None:
    SubBudTUI().run()


if __name__ == "__main__":
    main()
