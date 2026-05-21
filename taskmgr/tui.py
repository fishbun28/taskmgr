#!/usr/bin/env python3
"""Taskmgr TUI - Interactive task manager interface."""

from datetime import datetime

from textual.app import App, ComposeResult
from textual.containers import Horizontal, Vertical
from textual.reactive import reactive
from textual.screen import ModalScreen
from textual.widgets import Button, DataTable, Footer, Header, Input, Label, Static

from taskmgr.core import (
    ensure_dirs,
    is_already_wrapped,
    contains_shell_syntax,
    suggest_shell_wrap,
    load_metadata,
    save_metadata,
    sanitize_name,
    unit_name,
    parse_schedule,
    write_unit,
    run_systemctl,
    SYSTEMD_USER_DIR,
)


class TaskEditScreen(ModalScreen):
    """Modal screen for adding or editing a task."""

    BINDINGS = [("escape", "close", "Close")]

    CSS = """
    TaskEditScreen {
        align: center middle;
    }
    #dialog {
        width: 60;
        height: auto;
        border: thick $background 80%;
        background: $surface;
        padding: 1 2;
    }
    #dialog Input {
        margin: 1 0;
    }
    #dialog Button {
        margin: 1 1;
    }
    """

    def __init__(self, task_name: str = "", task_data: dict | None = None):
        self.task_name = task_name
        self.task_data = task_data or {}
        super().__init__()

    def compose(self) -> ComposeResult:
        with Vertical(id="dialog"):
            yield Label("Edit Task" if self.task_data else "Add Task")
            yield Input(
                placeholder="Task name",
                value=self.task_name,
                id="name",
                disabled=bool(self.task_data),
            )
            yield Input(
                placeholder="Schedule (e.g. daily)",
                value=self.task_data.get("schedule", ""),
                id="schedule",
            )
            yield Input(
                placeholder="Command to execute",
                value=self.task_data.get("exec", ""),
                id="exec",
            )
            yield Input(
                placeholder="Description (optional)",
                value=self.task_data.get("description", ""),
                id="desc",
            )
            with Horizontal():
                yield Button("Save", variant="primary", id="save")
                yield Button("Cancel", id="cancel")

    def action_close(self):
        self.dismiss(None)

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "save":
            name = self.query_one("#name", Input).value.strip()
            schedule = self.query_one("#schedule", Input).value.strip()
            exec_cmd = self.query_one("#exec", Input).value.strip()
            desc = self.query_one("#desc", Input).value.strip()

            if not name or not schedule or not exec_cmd:
                self.notify("Name, schedule and command are required.", severity="error")
                return

            self.dismiss({
                "name": name,
                "schedule": schedule,
                "exec": exec_cmd,
                "description": desc,
            })
        else:
            self.dismiss(None)


class ConfirmScreen(ModalScreen):
    """Modal screen for confirmation."""

    BINDINGS = [("escape", "close", "Close")]

    CSS = """
    ConfirmScreen {
        align: center middle;
    }
    #dialog {
        width: 50;
        height: auto;
        border: thick $background 80%;
        background: $surface;
        padding: 1 2;
    }
    #dialog Button {
        margin: 1 1;
    }
    """

    def __init__(self, message: str):
        self.message = message
        super().__init__()

    def compose(self) -> ComposeResult:
        with Vertical(id="dialog"):
            yield Label(self.message)
            with Horizontal():
                yield Button("Confirm", variant="error", id="confirm")
                yield Button("Cancel", id="cancel")

    def action_close(self):
        self.dismiss(False)

    def on_button_pressed(self, event: Button.Pressed) -> None:
        self.dismiss(event.button.id == "confirm")


class LogScreen(ModalScreen):
    """Modal screen for viewing logs."""

    BINDINGS = [("escape", "close", "Close")]

    CSS = """
    LogScreen {
        align: center middle;
    }
    #dialog {
        width: 80;
        height: 24;
        border: thick $background 80%;
        background: $surface;
        padding: 1 2;
    }
    #log-content {
        height: 1fr;
        scrollbar-size: 1 1;
    }
    """

    def __init__(self, logs: str):
        self.logs = logs
        super().__init__()

    def compose(self) -> ComposeResult:
        with Vertical(id="dialog"):
            yield Static(self.logs, id="log-content")
            yield Button("Close", id="close")

    def action_close(self):
        self.dismiss()

    def on_button_pressed(self, event: Button.Pressed) -> None:
        self.dismiss()


class TaskmgrApp(App):
    """Main TUI application."""

    CSS = """
    Screen { align: center middle; }
    DataTable { height: 1fr; }
    #empty { display: none; }
    """

    BINDINGS = [
        ("q", "quit", "Quit"),
        ("a", "add_task", "Add"),
        ("e", "edit_task", "Edit"),
        ("d", "delete_task", "Delete"),
        ("r", "run_task", "Run"),
        ("l", "view_logs", "Logs"),
        ("R", "refresh_table", "Refresh"),
    ]

    task_rows: reactive[list[dict]] = reactive([])

    def compose(self) -> ComposeResult:
        yield Header()
        yield DataTable()
        yield Static("No tasks found. Press 'a' to add one.", id="empty")
        yield Footer()

    def on_mount(self) -> None:
        ensure_dirs()
        table = self.query_one(DataTable)
        table.cursor_type = "row"
        table.add_columns("Name", "Schedule", "Command", "Status", "Next Run")
        self.refresh_table()

    def fetch_task_rows(self) -> list[dict]:
        meta = load_metadata()
        rows = []
        for key, task in sorted(meta["tasks"].items(), key=lambda x: x[1]["name"]):
            uname = unit_name(task["name"])
            timer_unit = f"{uname}.timer"
            service_path = SYSTEMD_USER_DIR / f"{uname}.service"

            result = run_systemctl(
                "show", timer_unit,
                "-p", "ActiveState",
                "-p", "UnitFileState",
            )
            props = {}
            for line in result.stdout.strip().split("\n"):
                if "=" in line:
                    k, v = line.split("=", 1)
                    props[k] = v

            active = props.get("ActiveState", "unknown")
            file_state = props.get("UnitFileState", "")

            if active == "active":
                status = "active"
            elif file_state == "enabled":
                status = "waiting"
            elif service_path.exists():
                status = "disabled"
            else:
                status = "broken"

            next_run = "-"
            if active == "active":
                lr = run_systemctl("show", timer_unit, "-p", "NextElapseUSecRealtime")
                for line in lr.stdout.strip().split("\n"):
                    if line.startswith("NextElapseUSecRealtime="):
                        val = line.split("=", 1)[1]
                        if val:
                            next_run = val
                        break

            cmd = task["exec"]
            if len(cmd) > 40:
                cmd = cmd[:37] + "..."

            rows.append({
                "key": key,
                "name": task["name"],
                "schedule": task["parsed_schedule"],
                "cmd": cmd,
                "status": status,
                "next_run": next_run,
                "raw": task,
            })
        return rows

    def refresh_table(self):
        table = self.query_one(DataTable)
        table.clear()
        self.task_rows = self.fetch_task_rows()
        for task in self.task_rows:
            table.add_row(
                task["name"],
                task["schedule"],
                task["cmd"],
                task["status"],
                task["next_run"],
            )
        empty = self.query_one("#empty", Static)
        empty.styles.display = "block" if not self.task_rows else "none"

    def get_selected_task(self) -> dict | None:
        table = self.query_one(DataTable)
        if table.cursor_row is None or not self.task_rows:
            return None
        if table.cursor_row >= len(self.task_rows):
            return None
        return self.task_rows[table.cursor_row]

    def action_refresh_table(self):
        self.refresh_table()
        self.notify("Refreshed.")

    def _write_task(self, result: dict, overwrite: bool = False) -> bool:
        """Shared logic to validate and persist a task. Returns True on success."""
        try:
            parsed = parse_schedule(result["schedule"])
        except ValueError as e:
            self.notify(f"Invalid schedule: {e}", severity="error")
            return False

        exec_cmd = result["exec"]
        if not is_already_wrapped(exec_cmd) and contains_shell_syntax(exec_cmd):
            exec_cmd = suggest_shell_wrap(exec_cmd)

        uname = unit_name(result["name"])

        if not overwrite:
            meta = load_metadata()
            if sanitize_name(result["name"]) in meta["tasks"]:
                self.notify(f"Task '{result['name']}' already exists.", severity="error")
                return False

        write_unit(result["name"], parsed, exec_cmd, result["description"])

        meta = load_metadata()
        meta["tasks"][sanitize_name(result["name"])] = {
            "name": result["name"],
            "description": result["description"],
            "schedule": result["schedule"],
            "parsed_schedule": parsed,
            "exec": result["exec"],
            "created_at": datetime.now().isoformat(),
        }
        save_metadata(meta)

        run_systemctl("daemon-reload")
        run_systemctl("enable", "--now", f"{uname}.timer")
        return True

    def action_add_task(self):
        def on_result(result):
            if not result:
                return
            if self._write_task(result):
                self.refresh_table()
                self.notify(f"Task '{result['name']}' added.")

        self.push_screen(TaskEditScreen(), on_result)

    def action_edit_task(self):
        task = self.get_selected_task()
        if task is None:
            self.notify("No task selected.", severity="warning")
            return

        def on_result(result):
            if not result:
                return
            try:
                parsed = parse_schedule(result["schedule"])
            except ValueError as e:
                self.notify(f"Invalid schedule: {e}", severity="error")
                return

            exec_cmd = result["exec"]
            if not is_already_wrapped(exec_cmd) and contains_shell_syntax(exec_cmd):
                exec_cmd = suggest_shell_wrap(exec_cmd)

            write_unit(result["name"], parsed, exec_cmd, result["description"])

            meta = load_metadata()
            key = sanitize_name(result["name"])
            old_schedule = meta["tasks"][key].get("schedule")
            meta["tasks"][key] = {
                "name": result["name"],
                "description": result["description"],
                "schedule": result["schedule"],
                "parsed_schedule": parsed,
                "exec": result["exec"],
                "updated_at": datetime.now().isoformat(),
            }
            save_metadata(meta)

            run_systemctl("daemon-reload")
            if result["schedule"] != old_schedule:
                run_systemctl("restart", f"{unit_name(result['name'])}.timer")
            self.refresh_table()
            self.notify(f"Task '{result['name']}' updated.")

        self.push_screen(TaskEditScreen(task["name"], task["raw"]), on_result)

    def action_delete_task(self):
        task = self.get_selected_task()
        if task is None:
            self.notify("No task selected.", severity="warning")
            return
        name = task["name"]
        uname = unit_name(name)

        def on_confirm(confirmed):
            if not confirmed:
                return
            run_systemctl("disable", "--now", f"{uname}.timer", check=False)
            (SYSTEMD_USER_DIR / f"{uname}.timer").unlink(missing_ok=True)
            (SYSTEMD_USER_DIR / f"{uname}.service").unlink(missing_ok=True)

            meta = load_metadata()
            meta["tasks"].pop(sanitize_name(name), None)
            save_metadata(meta)
            run_systemctl("daemon-reload")
            self.refresh_table()
            self.notify(f"Task '{name}' removed.")

        self.push_screen(ConfirmScreen(f"Delete task '{name}'?"), on_confirm)

    def action_run_task(self):
        task = self.get_selected_task()
        if task is None:
            self.notify("No task selected.", severity="warning")
            return
        name = task["name"]
        uname = unit_name(name)
        result = run_systemctl("start", f"{uname}.service")
        if result.returncode == 0:
            self.notify(f"Task '{name}' started.")
        else:
            self.notify(f"Failed to start: {result.stderr.strip()}", severity="error")

    def action_view_logs(self):
        task = self.get_selected_task()
        if task is None:
            self.notify("No task selected.", severity="warning")
            return
        name = task["name"]
        uname = unit_name(name)
        result = run_systemctl("status", f"{uname}.service", check=False)
        logs = result.stdout or result.stderr or "No output."
        self.push_screen(LogScreen(logs))


def main():
    app = TaskmgrApp()
    app.run()
