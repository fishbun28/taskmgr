#!/usr/bin/env python3
"""Taskmgr CLI - Personal task manager via systemd timers."""

import subprocess
from datetime import datetime

import click
from rich.console import Console
from rich.table import Table

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

console = Console()


def systemctl(*args, check=True):
    """Wrapper around run_systemctl that raises Click exceptions on failure."""
    result = run_systemctl(*args)
    if check and result.returncode != 0:
        err = result.stderr.strip() or result.stdout.strip()
        console.print(f"[red]systemctl error:[/red] {err}")
        raise click.ClickException("systemctl command failed")
    return result


@click.group()
def cli():
    """Personal task manager via systemd timers."""
    ensure_dirs()


@cli.command()
@click.argument("name")
@click.option(
    "--schedule", "-s", required=True,
    help="Schedule: preset (daily/hourly/weekly), cron (0 2 * * *), or systemd OnCalendar",
)
@click.option("--exec", "-e", required=True, help="Command to execute")
@click.option("--desc", "-d", default="", help="Description")
def add(name, schedule, exec, desc):
    """Add a new scheduled task."""
    uname = unit_name(name)
    timer_file = SYSTEMD_USER_DIR / f"{uname}.timer"

    if timer_file.exists():
        if not click.confirm(f"Task '{name}' already exists. Overwrite?"):
            raise click.Abort()

    try:
        parsed = parse_schedule(schedule)
    except ValueError as e:
        raise click.BadParameter(str(e))

    # Warn about shell syntax in ExecStart
    if not is_already_wrapped(exec) and contains_shell_syntax(exec):
        suggested = suggest_shell_wrap(exec)
        console.print("[yellow]⚠ Warning:[/yellow] Command contains shell syntax (redirects, pipes, etc.)")
        console.print("   [dim]systemd ExecStart does not support shell features directly.[/dim]")
        console.print(f"   Suggested fix: [cyan]{suggested}[/cyan]")
        if not click.confirm("Continue with original command anyway?"):
            console.print("[dim]Aborted. Use the suggested command or wrap it manually.[/dim]")
            raise click.Abort()

    write_unit(name, parsed, exec, desc)

    meta = load_metadata()
    meta["tasks"][sanitize_name(name)] = {
        "name": name,
        "description": desc,
        "schedule": schedule,
        "parsed_schedule": parsed,
        "exec": exec,
        "created_at": datetime.now().isoformat(),
    }
    save_metadata(meta)

    systemctl("daemon-reload")
    systemctl("enable", "--now", f"{uname}.timer")

    console.print(f"[green]✓[/green] Task [bold]{name}[/bold] added and enabled.")
    console.print(f"   Schedule: [cyan]{parsed}[/cyan]")
    console.print(f"   Command:  [yellow]{exec}[/yellow]")


@cli.command()
@click.argument("name")
@click.option("--schedule", "-s", default=None, help="New schedule")
@click.option("--exec", "-e", default=None, help="New command")
@click.option("--desc", "-d", default=None, help="New description")
def edit(name, schedule, exec, desc):
    """Edit an existing task."""
    uname = unit_name(name)
    timer = SYSTEMD_USER_DIR / f"{uname}.timer"

    if not timer.exists():
        raise click.ClickException(f"Task '{name}' not found.")

    meta = load_metadata()
    key = sanitize_name(name)
    if key not in meta["tasks"]:
        raise click.ClickException(f"Task '{name}' not found in metadata.")

    task = meta["tasks"][key]

    # Merge with existing values
    new_schedule = schedule if schedule is not None else task["schedule"]
    new_exec = exec if exec is not None else task["exec"]
    new_desc = desc if desc is not None else task["description"]

    # If nothing changed
    if new_schedule == task["schedule"] and new_exec == task["exec"] and new_desc == task["description"]:
        console.print("[dim]No changes made.[/dim]")
        return

    # Parse new schedule
    try:
        parsed = parse_schedule(new_schedule)
    except ValueError as e:
        raise click.BadParameter(str(e))

    # Shell syntax check for new command
    if new_exec != task["exec"] and not is_already_wrapped(new_exec) and contains_shell_syntax(new_exec):
        suggested = suggest_shell_wrap(new_exec)
        console.print("[yellow]⚠ Warning:[/yellow] New command contains shell syntax (redirects, pipes, etc.)")
        console.print("   [dim]systemd ExecStart does not support shell features directly.[/dim]")
        console.print(f"   Suggested fix: [cyan]{suggested}[/cyan]")
        if not click.confirm("Continue with new command anyway?"):
            console.print("[dim]Aborted.[/dim]")
            raise click.Abort()

    # Write updated units
    write_unit(name, parsed, new_exec, new_desc)

    # Update metadata
    task["schedule"] = new_schedule
    task["parsed_schedule"] = parsed
    task["exec"] = new_exec
    task["description"] = new_desc
    task["updated_at"] = datetime.now().isoformat()
    save_metadata(meta)

    systemctl("daemon-reload")

    # Restart timer if schedule changed so systemd picks up the new OnCalendar
    if schedule is not None:
        systemctl("restart", f"{uname}.timer")

    console.print(f"[green]✓[/green] Task [bold]{name}[/bold] updated.")
    if schedule is not None:
        console.print(f"   Schedule: [cyan]{parsed}[/cyan]")
    if exec is not None:
        console.print(f"   Command:  [yellow]{new_exec}[/yellow]")


@cli.command()
@click.argument("name")
def remove(name):
    """Remove a task."""
    uname = unit_name(name)
    timer = SYSTEMD_USER_DIR / f"{uname}.timer"
    service = SYSTEMD_USER_DIR / f"{uname}.service"

    if not timer.exists():
        raise click.ClickException(f"Task '{name}' not found.")

    systemctl("disable", "--now", f"{uname}.timer", check=False)

    timer.unlink(missing_ok=True)
    service.unlink(missing_ok=True)

    meta = load_metadata()
    meta["tasks"].pop(sanitize_name(name), None)
    save_metadata(meta)

    systemctl("daemon-reload")
    console.print(f"[green]✓[/green] Task [bold]{name}[/bold] removed.")


@cli.command(name="list")
def list_tasks():
    """List all tasks."""
    meta = load_metadata()
    if not meta["tasks"]:
        console.print("[dim]No tasks found.[/dim]")
        return

    table = Table(title="Taskmgr Tasks")
    table.add_column("Name", style="cyan", no_wrap=True)
    table.add_column("Schedule", style="green")
    table.add_column("Command", style="yellow")
    table.add_column("Status", style="white")
    table.add_column("Next Run", style="magenta")

    for key, task in sorted(meta["tasks"].items(), key=lambda x: x[1]["name"]):
        uname = unit_name(task["name"])
        timer_unit = f"{uname}.timer"
        service_path = SYSTEMD_USER_DIR / f"{uname}.service"

        # Get state from systemd
        result = systemctl(
            "show", timer_unit,
            "-p", "ActiveState",
            "-p", "UnitFileState",
            check=False,
        )
        props = {}
        for line in result.stdout.strip().split("\n"):
            if "=" in line:
                k, v = line.split("=", 1)
                props[k] = v

        active = props.get("ActiveState", "unknown")
        file_state = props.get("UnitFileState", "")

        if active == "active":
            status = "[green]active[/green]"
        elif file_state == "enabled":
            status = "[yellow]waiting[/yellow]"
        elif service_path.exists():
            status = "[dim]disabled[/dim]"
        else:
            status = "[red]broken[/red]"

        # Get next run time
        next_run = "-"
        if active == "active":
            lr = systemctl(
                "show", timer_unit,
                "-p", "NextElapseUSecRealtime",
                check=False,
            )
            for line in lr.stdout.strip().split("\n"):
                if line.startswith("NextElapseUSecRealtime="):
                    val = line.split("=", 1)[1]
                    if val:
                        next_run = val
                    break

        cmd = task["exec"]
        if len(cmd) > 45:
            cmd = cmd[:42] + "..."

        table.add_row(
            task["name"],
            task["parsed_schedule"],
            cmd,
            status,
            next_run,
        )

    console.print(table)


@cli.command()
@click.argument("name")
@click.option("-f", "--follow", is_flag=True, help="Follow log output")
def logs(name, follow):
    """View logs for a task."""
    uname = unit_name(name)
    if not (SYSTEMD_USER_DIR / f"{uname}.service").exists():
        raise click.ClickException(f"Task '{name}' not found.")

    cmd = ["journalctl", "--user", "-u", f"{uname}.service"]
    if follow:
        cmd += ["-f"]
    else:
        cmd += ["-n", "100", "--no-pager"]

    subprocess.run(cmd)


@cli.command()
@click.argument("name")
def run(name):
    """Run a task immediately."""
    uname = unit_name(name)
    systemctl("start", f"{uname}.service")
    console.print(f"[green]✓[/green] Task [bold]{name}[/bold] started.")

    result = systemctl("status", f"{uname}.service", check=False)
    if result.stdout:
        console.print(result.stdout)


@cli.command()
@click.argument("name")
def enable(name):
    """Enable a task."""
    uname = unit_name(name)
    systemctl("enable", "--now", f"{uname}.timer")
    console.print(f"[green]✓[/green] Task [bold]{name}[/bold] enabled.")


@cli.command()
@click.argument("name")
def disable(name):
    """Disable a task."""
    uname = unit_name(name)
    systemctl("disable", "--now", f"{uname}.timer")
    console.print(f"[green]✓[/green] Task [bold]{name}[/bold] disabled.")


@cli.command()
def tui():
    """Launch interactive TUI."""
    from taskmgr.tui import main as tui_main
    tui_main()


def main():
    cli()


if __name__ == "__main__":
    main()
