"""Core business logic for taskmgr."""

import json
import os
import re
import shlex
import subprocess
from pathlib import Path

SYSTEMD_USER_DIR = Path.home() / ".config" / "systemd" / "user"
TASKMGR_CONFIG_DIR = Path.home() / ".config" / "taskmgr"
METADATA_FILE = TASKMGR_CONFIG_DIR / "tasks.json"

DOW_MAP = {
    "0": "Sun", "1": "Mon", "2": "Tue", "3": "Wed",
    "4": "Thu", "5": "Fri", "6": "Sat", "7": "Sun",
}
PRESETS = {"hourly", "daily", "weekly", "monthly", "yearly", "quarterly", "semiannually"}


def ensure_dirs():
    SYSTEMD_USER_DIR.mkdir(parents=True, exist_ok=True)
    TASKMGR_CONFIG_DIR.mkdir(parents=True, exist_ok=True)


# Patterns that require a shell interpreter (systemd ExecStart does not support these)
SHELL_PATTERN = re.compile(r'(\|\|)|(&&)|(\|)|(;)|(`)|(\$\()|(>>)|(<<)|([<>])')


def is_already_wrapped(cmd: str) -> bool:
    """Check if command is already wrapped in a shell interpreter."""
    cmd_lower = cmd.strip().lower()
    shells = (
        "/bin/sh", "/bin/bash", "/bin/dash", "/bin/zsh", "/bin/fish",
        "/usr/bin/sh", "/usr/bin/bash", "/usr/bin/dash", "/usr/bin/zsh", "/usr/bin/fish",
        "/usr/local/bin/sh", "/usr/local/bin/bash", "/usr/local/bin/fish",
        "sh", "bash", "dash", "zsh", "fish",
    )
    for shell in shells:
        if cmd_lower.startswith(f"{shell} -c"):
            return True
    return False


def contains_shell_syntax(cmd: str) -> bool:
    """Check if command contains shell metacharacters unsupported by systemd ExecStart."""
    return bool(SHELL_PATTERN.search(cmd))


def suggest_shell_wrap(cmd: str) -> str:
    """Suggest a shell-wrapped version of the command."""
    return f'/bin/sh -c {shlex.quote(cmd)}'


def load_metadata() -> dict:
    if not METADATA_FILE.exists():
        return {"tasks": {}}
    with open(METADATA_FILE, "r", encoding="utf-8") as f:
        return json.load(f)


def save_metadata(data: dict):
    with open(METADATA_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)


def sanitize_name(name: str) -> str:
    normalized = name.replace(" ", "-").lower()
    return "".join(c if c.isalnum() or c in "-_" else "-" for c in normalized)


def unit_name(name: str) -> str:
    return f"taskmgr-{sanitize_name(name)}"


def validate_field(field: str, label: str, allow_step: bool = False):
    if field == "*":
        return
    if "/" in field:
        if not allow_step:
            raise ValueError(f"{label} step not supported: {field}")
        base, step = field.split("/")
        if base != "*":
            raise ValueError(f"{label} step must be */N: {field}")
        if not step.isdigit() or int(step) < 1:
            raise ValueError(f"{label} step must be positive integer: {field}")
    elif not field.isdigit():
        raise ValueError(f"{label} must be *, number, or */N: {field}")


def convert_cron(parts: list[str]) -> str:
    m, h, dom, mon, dow = parts
    joined = " ".join(parts)

    if joined == "0 * * * *":
        return "hourly"
    if joined == "0 0 * * *":
        return "daily"
    if joined == "0 0 * * 0":
        return "weekly"
    if joined == "0 0 1 * *":
        return "monthly"

    validate_field(m, "minute", allow_step=True)
    validate_field(h, "hour", allow_step=True)
    validate_field(dom, "day-of-month", allow_step=False)
    validate_field(mon, "month", allow_step=False)
    validate_field(dow, "weekday", allow_step=False)

    if dow != "*" and dom != "*":
        raise ValueError(
            "Cron with both weekday and day-of-month is not supported. "
            "Use systemd OnCalendar syntax directly with --schedule."
        )

    weekday_part = ""
    if dow != "*":
        if dow in DOW_MAP:
            weekday_part = DOW_MAP[dow] + " "
        else:
            raise ValueError(f"weekday must be 0-6: {dow}")

    mon_str = mon.zfill(2) if mon != "*" else "*"
    dom_str = dom.zfill(2) if dom != "*" else "*"
    date_part = f"*-{mon_str}-{dom_str}"

    def fmt_time(f):
        if f == "*":
            return "*"
        if f.startswith("*/"):
            return f"00/{f[2:]}"
        return f.zfill(2)

    time_part = f"{fmt_time(h)}:{fmt_time(m)}:00"
    return f"{weekday_part}{date_part} {time_part}"


def parse_schedule(schedule: str) -> str:
    schedule = schedule.strip()
    if schedule.lower() in PRESETS:
        return schedule.lower()

    parts = schedule.split()
    if len(parts) == 5:
        return convert_cron(parts)

    return schedule


def write_unit(name: str, schedule: str, exec_cmd: str, description: str = ""):
    uname = unit_name(name)
    service_path = SYSTEMD_USER_DIR / f"{uname}.service"
    timer_path = SYSTEMD_USER_DIR / f"{uname}.timer"

    desc = description or name
    service_content = f"""[Unit]
Description=Taskmgr: {desc}

[Service]
Type=oneshot
ExecStart={exec_cmd}
StandardOutput=journal
StandardError=journal
"""

    timer_content = f"""[Unit]
Description=Timer for taskmgr: {name}

[Timer]
OnCalendar={schedule}
Persistent=true

[Install]
WantedBy=timers.target
"""

    with open(service_path, "w", encoding="utf-8") as f:
        f.write(service_content)
    with open(timer_path, "w", encoding="utf-8") as f:
        f.write(timer_content)


def run_systemctl(*args, check=True):
    """Run systemctl --user and return the CompletedProcess."""
    env = os.environ.copy()
    env["SYSTEMD_COLORS"] = "false"
    cmd = ["systemctl", "--user"] + list(args)
    return subprocess.run(cmd, capture_output=True, text=True, env=env)
