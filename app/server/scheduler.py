"""Scheduled task management — define, validate, generate systemd timers.

Tasks are stored in DayzConfig.scheduled_tasks as JSON-serializable dicts.
systemd .timer + .service files are built as f-strings here (no templating
library) and installed alongside the server's own unit.
"""

from __future__ import annotations

import json
import logging
import re
import shutil
import subprocess
import sys
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Callable

from croniter import croniter
from defusedxml.ElementTree import fromstring as _safe_fromstring

from app.server.service_control import UnitPathError, unit_exec_word, unit_value
from app.shared.paths import INSTALL_DIR
from app.shared.platform import IS_WINDOWS, has_systemd, sudo_prefix
from app.shared.systemd_scope import get_scope, server_unit, task_unit_prefix

_log = logging.getLogger(__name__)

# Task types and the zsm CLI sub-command each fires. These are the real
# command paths registered in app.__main__; the generated unit invokes them as
# ``python -m app --yes <command>`` (``--yes`` is the global non-interactive
# flag so destructive tasks like wipe don't block on a confirmation prompt).
# Exception: the Linux "restart" task triggers systemctl directly as root —
# see generate_service_content — because a non-root timer can't restart a
# system unit and sudo has no tty.
TASK_COMMANDS: dict[str, str] = {
    # What Windows' restart task runs. A systemd restart task calls systemctl
    # (generate_service_content), and `zsm supervise` restarts its own child.
    "restart": "restart --if-running",
    "update": "run update",
    "backup": "backup create",
    "mod_check": "mods check-updates",
    # A manual wipe refuses a running server; the scheduled one stops it,
    # wipes and starts it again, like `run update`.
    "wipe": "cleanup wipe-server --restart",
    # These two carry a payload, so their unit runs `scheduler run <id>` and
    # the payload is read back out of config.json — see PAYLOAD_TASK_TYPES.
    "announce": "scheduler run",
    "rcon": "scheduler run",
}

TASK_TYPE_LABELS: dict[str, str] = {
    "restart": "Server Restart",
    "update": "Server + Mod Update",
    "backup": "Quick Backup",
    "mod_check": "Mod Update Check",
    "wipe": "Server Wipe",
    "announce": "Announcement",
    "rcon": "RCon Command",
}

# Task types whose behaviour comes from `task.options`, and the one key each
# one reads. **The payload never reaches the generated unit.** `ExecStart=` is
# parsed by systemd — not a shell, but it has its own quoting rules and `%`
# specifiers, so an announcement containing `%` or `"` would be mangled or
# refuse to parse. Their unit runs `zsm scheduler run <task id>` instead (the
# id is `_SAFE_ID_RE`-validated, so it is safe as argv) and the command reads
# the text back out of config.json. Nothing is ever passed through a shell.
PAYLOAD_TASK_TYPES: dict[str, str] = {
    "announce": "message",
    "rcon": "command",
}

# A BattlEye RCon packet is not large, and a scheduled task is not the place
# to discover that limit. Generous but bounded.
MAX_PAYLOAD_CHARS = 512

# Task types that take the server down, and which the restart-warning
# countdown therefore has to count toward. Add a type here when it stops the
# server — the warning chain follows automatically, and forgetting to is how
# a task ends up stopping a server with nobody warned.
RESTART_TASK_TYPES: frozenset[str] = frozenset({"restart", "update", "wipe"})


def cron_now() -> datetime:
    """The anchor every cron calculation in this module starts from.

    **Local time, not UTC.** A cron expression here ends up in two places that
    both read it as local: systemd's ``OnCalendar`` (``cron_to_oncalendar``
    emits bare wall-clock fields, and systemd resolves those against the
    machine's timezone) and Windows' scheduled task. Anchoring croniter at
    ``datetime.now(timezone.utc)`` meant this module answered "when does
    ``0 6 * * *`` next fire?" with 06:00 **UTC** — two hours late on a CEST
    box, and the answer the Scheduler page's "Next run" column has been
    showing. Aware, not naive, so the arithmetic against an aware ``now``
    elsewhere still works.
    """
    return datetime.now().astimezone()


def cron_time_zone(now: datetime | None = None) -> str:
    """The zone ``cron_now()`` is in, as the Scheduler page names it.

    ``"UTC"`` for a zero offset, else the offset with the zone's name when
    the machine has one: ``"CEST (UTC+2)"``, ``"UTC+5:30"``. Every time on
    that page is this zone's wall clock, and a browser in another zone reads
    the wrong hour without it.
    """
    now = now or cron_now()
    minutes = int((now.utcoffset() or timedelta(0)).total_seconds() // 60)
    if minutes == 0:
        return "UTC"
    sign = "+" if minutes > 0 else "-"
    hours, rest = divmod(abs(minutes), 60)
    offset = f"UTC{sign}{hours}" + (f":{rest:02d}" if rest else "")
    name = now.tzname() or ""
    # A zone without an abbreviation reports its offset ("+0530") as its name.
    if not name or name[0] in "+-" or name.startswith("UTC"):
        return offset
    return f"{name} ({offset})"


# ---------------------------------------------------------------------------
# Data model
# ---------------------------------------------------------------------------


@dataclass
class ScheduledTask:
    """A recurring scheduled task definition."""

    id: str
    task_type: str  # restart, backup, mod_check, wipe
    cron: str  # cron expression "0 */4 * * *"
    enabled: bool = True
    options: dict[str, Any] = field(default_factory=dict)
    last_run: str | None = None
    last_status: str | None = None

    @property
    def label(self) -> str:
        return TASK_TYPE_LABELS.get(self.task_type, self.task_type)

    @property
    def description(self) -> str:
        """Human-readable schedule description."""
        try:
            from cron_descriptor import get_description
            return get_description(self.cron)
        except Exception:
            return self.cron

    @property
    def next_run(self) -> datetime | None:
        """Next firing, as a timezone-aware datetime in **local** time."""
        try:
            cron = croniter(self.cron, cron_now())
            return cron.get_next(datetime)
        except (ValueError, KeyError):
            return None

    @property
    def next_run_display(self) -> str:
        """Human-readable time until next run."""
        nxt = self.next_run
        if nxt is None:
            return "unknown"
        delta = nxt - cron_now()
        secs = int(delta.total_seconds())
        if secs < 0:
            return "now"
        if secs < 3600:
            return f"{secs // 60}m"
        if secs < 86400:
            return f"{secs // 3600}h {(secs % 3600) // 60}m"
        return f"{secs // 86400}d {(secs % 86400) // 3600}h"

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict) -> ScheduledTask:
        return cls(**{k: v for k, v in data.items() if k in cls.__dataclass_fields__})


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------


def validate_cron(expr: str) -> bool:
    """Check if a cron expression is valid: exactly five fields.

    ``croniter`` also takes a sixth (seconds) field, but ``cron_to_oncalendar``
    converts five, and a six-field expression fell through to
    ``_fallback_oncalendar``: an hourly timer at whatever minute it was
    generated, while the page showed "every second" (test pass 2, N18).

    It must also be one ``cron_to_oncalendar`` can turn into a timer: croniter
    takes ``W`` and an ``L`` inside a list, which systemd has no way to say
    (``cron_systemd_problem``). On Windows it must also become a scheduled
    task (``cron_platform_problem``). ``cron_problem`` says which rule failed.
    """
    return cron_problem(expr) is None


def cron_problem(expr: str) -> str | None:
    """Why *expr* cannot be a scheduled task here, in words, or None.

    What a refused save says after "Invalid cron expression", and what
    ``POST /api/schedule/validate-cron`` hands the Scheduler page to show
    while the expression is typed (plans/68 B24).
    """
    if len(expr.split()) != 5:
        return "a schedule has five fields: minute, hour, day, month and weekday"
    if not croniter.is_valid(expr):
        return "a field is not cron syntax or is out of range"
    return _never_comes_problem(expr) or cron_platform_problem(expr)


def _never_comes_problem(expr: str) -> str | None:
    """Why *expr* names a date no month has (``0 0 31 2 *``), or None.

    croniter takes it as valid and then finds no next run, so the page showed
    no "Next run" and the timer never fired. Only a day list of plain numbers
    with every weekday is judged here; a weekday as well is
    ``_day_or_weekday_problem``'s to refuse.
    """
    fields, _nth = croniter.expand(expr)
    days, months, weekdays = fields[2], fields[3], fields[4]
    if days[0] == "*" or weekdays[0] != "*" or not all(isinstance(d, int) for d in days):
        return None
    month_list = range(1, 13) if months[0] == "*" else months
    if min(days) > max(_MONTH_DAYS[m - 1] for m in month_list):
        return "no month given has that day, so it would never run"
    return None


def cron_platform_problem(expr: str) -> str | None:
    """Why a croniter-valid five-field *expr* cannot be scheduled here, or None.

    The systemd rules apply on every machine, so a schedule saved on one
    still installs after a move to another; on Windows, Task Scheduler's own
    are added (``cron_schtasks_problem``, plans/68 B22). The one validator
    ``validate_cron`` and ``validate_task`` share.
    """
    return cron_systemd_problem(expr) or (cron_schtasks_problem(expr) if IS_WINDOWS else None)


# Checked with ``fullmatch``: ``match`` with ``$`` also accepts the id followed
# by a trailing newline, and the id is a unit file name and part of
# ``ExecStart=`` (plans/68 INJ-5).
_SAFE_ID_RE = re.compile(r"[a-zA-Z0-9_-]+")


def validate_task(task: ScheduledTask) -> list[str]:
    """Validate a task definition. Returns list of error messages."""
    errors: list[str] = []
    if not task.id:
        errors.append("Task ID is required")
    elif not _SAFE_ID_RE.fullmatch(task.id):
        errors.append("Task ID must contain only letters, digits, hyphens, and underscores")
    if task.task_type not in TASK_COMMANDS:
        errors.append(f"Unknown task type: {task.task_type}")
    problem = cron_problem(task.cron)
    if problem:
        errors.append(f"Invalid cron expression: {task.cron} ({problem})")
    errors.extend(_validate_payload(task))
    return errors


def _validate_payload(task: ScheduledTask) -> list[str]:
    """Check the `options` payload of a payload-carrying task type.

    Validated here rather than at the point of use so a task that could never
    do anything cannot be created in the first place — the timer would
    otherwise fire nightly and exit having sent an empty string.
    """
    key = PAYLOAD_TASK_TYPES.get(task.task_type)
    if key is None:
        return []
    value = (task.options or {}).get(key, "")
    if not isinstance(value, str) or not value.strip():
        # Pluralised to dodge a/an on a label that varies ("Announcement",
        # "RCon Command").
        return [f"{TASK_TYPE_LABELS[task.task_type]} tasks need a {key}"]
    if len(value) > MAX_PAYLOAD_CHARS:
        return [f"{key} is longer than {MAX_PAYLOAD_CHARS} characters"]
    # A newline would split one RCon command into two, which is the closest
    # thing to injection this path has.
    if "\n" in value or "\r" in value:
        return [f"{key} must be a single line"]
    return []


def task_payload(task: ScheduledTask) -> str:
    """The text a payload-carrying task acts on, or "" for other types."""
    key = PAYLOAD_TASK_TYPES.get(task.task_type)
    if key is None:
        return ""
    return str((task.options or {}).get(key, "")).strip()


def task_command(task: ScheduledTask) -> str:
    """The `zsm` sub-command this task's unit should run.

    Payload types get their **task id** appended, not their payload — the text
    stays in config.json and never enters an `ExecStart=` line.
    """
    command = TASK_COMMANDS.get(task.task_type, "")
    if task.task_type in PAYLOAD_TASK_TYPES:
        return f"{command} {task.id}"
    return command


# ---------------------------------------------------------------------------
# CRUD — operates on DayzConfig.scheduled_tasks
# ---------------------------------------------------------------------------


def load_tasks(config) -> list[ScheduledTask]:
    """Load scheduled tasks from config."""
    return [ScheduledTask.from_dict(t) for t in config.scheduled_tasks]


def save_tasks(config, tasks: list[ScheduledTask]) -> None:
    """Save scheduled tasks to config."""
    config.scheduled_tasks = [t.to_dict() for t in tasks]
    config.save()


def apply_add_task(config, task: ScheduledTask) -> ScheduledTask:
    """Add a task to ``config.scheduled_tasks`` in memory (no ``config.save()``).

    Raises ``ValueError`` on a duplicate id or an invalid task. Returns the
    added task. This is the save-free core the web layer runs inside
    ``WebState.mutate_config`` (which owns the disk-reload + save under a lock).
    """
    tasks = load_tasks(config)
    if any(t.id == task.id for t in tasks):
        raise ValueError(f"Task with ID '{task.id}' already exists")
    errors = validate_task(task)
    if errors:
        raise ValueError("; ".join(errors))
    tasks.append(task)
    config.scheduled_tasks = [t.to_dict() for t in tasks]
    return task


def apply_update_task(config, task_id: str, **kwargs) -> ScheduledTask | None:
    """Update a task's fields in memory (no ``config.save()``).

    Raises ``ValueError`` if the resulting task is invalid. Returns the updated
    task, or ``None`` if no task has ``task_id``. Save-free counterpart to
    ``update_task`` for use inside ``WebState.mutate_config``.
    """
    tasks = load_tasks(config)
    for t in tasks:
        if t.id == task_id:
            for key, value in kwargs.items():
                if hasattr(t, key):
                    setattr(t, key, value)
            errors = validate_task(t)
            if errors:
                raise ValueError("; ".join(errors))
            config.scheduled_tasks = [x.to_dict() for x in tasks]
            return t
    return None


def next_restart_occurrence(config, now: datetime | None = None) -> tuple[datetime, int] | None:
    """Soonest scheduled server restart, as ``(when, cycle_minutes)``.

    ``cycle_minutes`` is the gap back to that same task's previous firing —
    how long this restart cycle is. ``RestartWarningState.get_pending_warnings``
    takes it as ``restart_interval`` and drops thresholds longer than the cycle,
    so a task restarting every 10 minutes doesn't try to warn 15 minutes ahead.

    Returns ``None`` when no enabled task restarts the server, which is the
    signal to fall back to the messages.xml deadline (see
    ``WebNotifier._get_remaining_and_deadline``).

    A task whose timer units are not installed never fires, so it is skipped
    (night test 2026-11-22: "Next restart" counted down to one before Install
    timers). Unknown units (``timer_install_state`` returns ``{}``: Windows,
    ``zsm supervise``) skip nothing.
    """
    anchor = now or cron_now()
    best: tuple[datetime, int] | None = None
    tasks = [t for t in load_tasks(config) if t.enabled and t.task_type in RESTART_TASK_TYPES]
    states = timer_install_state(config)[0] if tasks else {}
    for task in tasks:
        if states.get(task.id) == TIMER_MISSING:
            continue
        try:
            nxt = croniter(task.cron, anchor).get_next(datetime)
            prev = croniter(task.cron, nxt).get_prev(datetime)
        except (ValueError, KeyError):
            continue  # an invalid cron is not a reason to warn about nothing
        cycle = max(1, int((nxt - prev).total_seconds() // 60))
        if best is None or nxt < best[0]:
            best = (nxt, cycle)
    return best


def apply_remove_task(config, task_id: str) -> bool:
    """Remove a task from ``config.scheduled_tasks`` in memory (no save).

    Returns ``True`` if a task was removed. Save-free counterpart to
    ``remove_task`` for use inside ``WebState.mutate_config``.
    """
    tasks = load_tasks(config)
    original = len(tasks)
    tasks = [t for t in tasks if t.id != task_id]
    if len(tasks) == original:
        return False
    config.scheduled_tasks = [t.to_dict() for t in tasks]
    return True


def add_task(config, task: ScheduledTask) -> None:
    """Add a new scheduled task and persist the config."""
    apply_add_task(config, task)
    config.save()


def update_task(config, task_id: str, **kwargs) -> ScheduledTask | None:
    """Update a task's fields and persist the config. Returns updated task or None."""
    task = apply_update_task(config, task_id, **kwargs)
    if task is not None:
        config.save()
    return task


def remove_task(config, task_id: str) -> bool:
    """Remove a task and persist the config. Returns True if found."""
    removed = apply_remove_task(config, task_id)
    if removed:
        config.save()
    return removed


def get_task(config, task_id: str) -> ScheduledTask | None:
    """Get a single task by ID."""
    for t in load_tasks(config):
        if t.id == task_id:
            return t
    return None


# ---------------------------------------------------------------------------
# Cron to systemd OnCalendar conversion
# ---------------------------------------------------------------------------


def cron_to_oncalendar(cron_expr: str) -> str:
    """Convert a cron expression to systemd OnCalendar format.

    Handles common patterns. Falls back to generating explicit timestamps
    for complex expressions.

    Cron: minute hour day month weekday
    OnCalendar: DayOfWeek Year-Month-Day Hour:Minute:Second
    """
    parts = cron_expr.strip().split()
    if len(parts) != 5:
        return _fallback_oncalendar(cron_expr)

    minute, hour, day, month, weekday = parts
    try:
        dow_part, date_part = _calendar_date(day, month, weekday)
    except ValueError:
        # validate_cron refuses these and no install path writes one; this
        # only keeps a page that compares unit text from raising.
        return _fallback_oncalendar(cron_expr)

    hour_cal = _convert_field(hour, pad=2, anchor="00")
    minute_cal = _convert_field(minute, pad=2, anchor="00")

    return f"{dow_part}{date_part} {hour_cal}:{minute_cal}:00"


class _NoSystemdEquivalent(ValueError):
    """A croniter-valid field that an ``OnCalendar=`` spec cannot express."""


_CRON_MONTHS = ("jan", "feb", "mar", "apr", "may", "jun",
                "jul", "aug", "sep", "oct", "nov", "dec")
_CRON_WEEKDAYS = ("sun", "mon", "tue", "wed", "thu", "fri", "sat")
_SYSTEMD_WEEKDAYS = ("Sun", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat")

# Map cron weekday to systemd (0=Sun in cron, Mon-Sun in systemd)
_DOW_MAP = {"0": "Sun", "1": "Mon", "2": "Tue", "3": "Wed",
            "4": "Thu", "5": "Fri", "6": "Sat", "7": "Sun"}


def cron_systemd_problem(expr: str) -> str | None:
    """Why a croniter-valid *expr* cannot become a systemd timer, or None.

    systemd's ``OnCalendar=`` has no nearest-weekday (``W``), cannot put a
    last day (``L``) in a list or range, and cannot narrow an nth or last
    weekday (``5#3``, ``L5``) by a day of the month as well (plans/68 B7). A
    stepped weekday range that runs past Sunday (``6-1/2``) has no one
    meaning to translate (B20), and neither has a day *or* a weekday (B21,
    ``_day_or_weekday_problem``).
    """
    parts = expr.split()
    if len(parts) != 5:
        return None
    try:
        _calendar_date(*parts[2:])
    except _NoSystemdEquivalent as e:
        return str(e)
    except ValueError:
        return "systemd timers cannot express this schedule"
    return _day_or_weekday_problem(expr)


# The most days each month can have (February in a leap year).
_MONTH_DAYS = (31, 29, 31, 30, 31, 30, 31, 31, 30, 31, 30, 31)


def _day_or_weekday_problem(expr: str) -> str | None:
    """The reason to refuse *expr* when it restricts both the day of the
    month and the weekday, or None (plans/68 B21).

    cron, and croniter, which the page's "Next run" and the restart countdown
    read, then run it when **either** field matches: ``0 4 1 * 1`` is the 1st
    and every Monday. ``OnCalendar=`` can only say both (``Mon *-*-1``, the
    1st when it is a Monday), and that is what the timer did. Refused rather
    than guessed at. The two readings agree only when both fields cover every
    day, so that passes. Asks croniter which fields are restricted, so it
    agrees with ``next_run`` (``*/2`` in the day field counts, ``?`` not).
    """
    try:
        fields, nth = croniter.expand(expr)
    except ValueError:
        return None  # not croniter-valid: validate_cron says so on its own
    days, months, weekdays = fields[2], fields[3], fields[4]
    if days[0] == "*" or weekdays[0] == "*":
        return None
    need = max(_MONTH_DAYS[m - 1] for m in (range(1, 13) if months[0] == "*" else months))
    if not nth and set(weekdays) >= set(range(7)) and set(days) >= set(range(1, need + 1)):
        return None
    return (
        "cron runs this when either the day or the weekday matches, and systemd "
        "timers only when both do; split it into two tasks, or write the first "
        "Monday of a month as 1#1"
    )


def _calendar_date(day: str, month: str, weekday: str) -> tuple[str, str]:
    """``(weekday prefix, date)`` of an ``OnCalendar=`` spec, e.g.
    ``("Fri ", "*-*-15..21")``. Raises ``_NoSystemdEquivalent``.

    The forms systemd has no word for are translated (plans/68 B7): month
    names become numbers, ``?`` is ``*``, a day of ``L`` is ``~01`` (the last
    day), ``5#3`` is a Friday on days 15..21, ``L5`` a Friday among the last
    seven days (``~07/1``), and a stepped weekday is spelled out. A weekday
    range through Sunday is put in systemd's Monday-first order (B20).
    """
    day = "*" if day == "?" else day
    weekday = "*" if weekday == "?" else weekday
    month_part = _convert_field(_month_numbers(month), pad=0, anchor="1")

    separator = "-"
    if day.upper() == "L":
        separator, day_part = "~", "01"
    elif "W" in day.upper():
        raise _NoSystemdEquivalent("systemd timers have no nearest-weekday 'W'")
    elif "L" in day.upper():
        raise _NoSystemdEquivalent(
            "systemd timers take a last day 'L' only on its own in the day field"
        )
    else:
        day_part = _convert_field(day, pad=0, anchor="1")

    if weekday == "*":
        return "", f"*-{month_part}{separator}{day_part}"

    nth = re.fullmatch(r"([0-7]|[a-z]{3})#([1-5])", weekday, re.IGNORECASE)
    last = re.fullmatch(r"L([0-7])", weekday, re.IGNORECASE)
    if nth or last:
        if day != "*":
            raise _NoSystemdEquivalent(
                "systemd timers cannot combine an nth or last weekday with a day of the month"
            )
        if nth:
            name = _SYSTEMD_WEEKDAYS[_weekday_number(nth.group(1)) % 7]
            k = int(nth.group(2))
            return f"{name} ", f"*-{month_part}-{(k - 1) * 7 + 1:02d}..{min(7 * k, 31):02d}"
        name = _SYSTEMD_WEEKDAYS[int(last.group(1)) % 7]
        return f"{name} ", f"*-{month_part}~07/1"
    if "#" in weekday or "L" in weekday.upper():
        raise _NoSystemdEquivalent(
            "systemd timers take an nth or last weekday only on its own in the weekday field"
        )

    mapped: list[str] = []
    for seg in weekday.split(","):
        if "/" in seg:
            mapped.extend(_weekday_step(seg))
        else:
            mapped.append(_map_dow(seg))
    return ",".join(mapped) + " ", f"*-{month_part}{separator}{day_part}"


def _map_dow(val: str) -> str:
    """Map a single cron weekday value or range to systemd format.

    systemd's week starts on Monday and a range may not run backwards, while
    cron's starts on Sunday, writes Sunday as 0 or 7 and wraps a range like
    croniter does (``5-1`` is Friday to Monday). A range systemd reads as the
    same days keeps its old spelling; any other is spelled out in systemd's
    order (plans/68 B20): ``0-7`` is every day (it was ``Sun..Sun``, Sundays
    only), ``0-3`` is ``Mon..Wed,Sun`` (``Sun..Wed`` was refused).
    """
    if "-" not in val:
        return _DOW_MAP.get(val, val.capitalize()[:3])
    lo_s, hi_s = val.split("-", 1)
    lo, hi = _weekday_number(lo_s), _weekday_number(hi_s)
    if lo <= hi:
        days = {d % 7 for d in range(lo, hi + 1)}
    else:
        days = {d % 7 for d in range(lo, 7)} | set(range(hi + 1))
    first, last = _monday_index(lo), _monday_index(hi)
    if first <= last and days == {d for d in range(7) if first <= _monday_index(d) <= last}:
        return f"{_DOW_MAP.get(lo_s, lo_s.capitalize()[:3])}..{_DOW_MAP.get(hi_s, hi_s.capitalize()[:3])}"
    return _weekday_runs(days)


def _monday_index(day: int) -> int:
    """A cron weekday (0 or 7 = Sunday) as systemd orders it, Monday first."""
    return (day + 6) % 7


def _weekday_runs(days: set[int]) -> str:
    """*days* (cron numbers) in systemd's order, a run of two or more as a
    range: ``{0, 1, 2, 5}`` is ``Mon..Tue,Fri,Sun``."""
    order = sorted(_monday_index(d) for d in days)
    runs: list[list[int]] = []
    for i in order:
        if runs and i == runs[-1][-1] + 1:
            runs[-1].append(i)
        else:
            runs.append([i])
    names = [_SYSTEMD_WEEKDAYS[(i + 1) % 7] for i in range(7)]  # Monday first
    return ",".join(
        names[r[0]] if len(r) == 1 else f"{names[r[0]]}..{names[r[-1]]}" for r in runs
    )


def _weekday_number(tok: str) -> int:
    return int(tok) if tok.isdigit() else _CRON_WEEKDAYS.index(tok.lower())


def _weekday_step(seg: str) -> list[str]:
    """A stepped weekday spelled out (systemd steps only numeric fields).
    Like croniter, an open start (``*/2``, ``1/2``) runs to Saturday, and
    ``7/2`` starts on Sunday as ``0/2`` does (it spelled out no day at all)."""
    base, _, step = seg.partition("/")
    if base == "*":
        lo, hi = 0, 6
    elif "-" in base:
        lo_s, hi_s = base.split("-", 1)
        lo, hi = _weekday_number(lo_s), _weekday_number(hi_s)
        if lo > hi:
            # croniter counts the step across the wrap differently from
            # cron arithmetic (6-1/2 is Saturday alone to croniter), and today
            # it wrote no weekday at all: every day. Nothing to translate to.
            raise _NoSystemdEquivalent(
                "systemd timers cannot step a weekday range that runs past "
                "Sunday; list the days instead"
            )
    else:
        lo, hi = _weekday_number(base) % 7, 6
    return list(dict.fromkeys(_SYSTEMD_WEEKDAYS[i % 7] for i in range(lo, hi + 1, int(step))))


def _month_numbers(val: str) -> str:
    """Month names as numbers: systemd's date takes numbers only."""
    return re.sub(
        r"[a-zA-Z]{3}",
        lambda m: str(_CRON_MONTHS.index(m.group(0).lower()) + 1)
        if m.group(0).lower() in _CRON_MONTHS else m.group(0),
        val,
    )


def _convert_field(val: str, *, pad: int, anchor: str) -> str:
    """Convert one numeric cron field to systemd OnCalendar syntax.

    Cron and systemd share comma lists and ``/`` steps, but cron writes ranges
    with ``-`` while systemd uses ``..`` — leaving ``-`` in place produces an
    invalid calendar spec (and in the month field the ``-`` even collides with
    the ``Year-Month-Day`` separator). systemd also rejects a ``*`` step base
    (cron ``*/N``), so it is anchored at the field start: ``00`` for time
    fields, ``1`` for day/month.
    """
    if val == "*":
        return "*"

    def _pad(tok: str) -> str:
        return tok.zfill(pad) if pad and tok.isdigit() else tok

    segments: list[str] = []
    for seg in val.split(","):
        base, step_sep, step = seg.partition("/")
        if "-" in base:
            lo, hi = base.split("-", 1)
            piece = f"{_pad(lo)}..{_pad(hi)}"
        elif base == "*":
            piece = anchor if step_sep else "*"
        else:
            piece = _pad(base)
        if step_sep:
            piece = f"{piece}/{step}"
        segments.append(piece)
    return ",".join(segments)


def _fallback_oncalendar(cron_expr: str) -> str:
    """Fallback for complex cron expressions: detect interval from first two firings."""
    try:
        cron = croniter(cron_expr)
        t1 = cron.get_next(datetime)
        t2 = cron.get_next(datetime)
        delta = t2 - t1
        hours = delta.total_seconds() / 3600
        if hours <= 1:
            return f"*-*-* *:{t1.minute:02d}:00"
        if hours <= 24 and hours == int(hours):
            return f"*-*-* 00/{int(hours)}:{t1.minute:02d}:00"
        return f"*-*-* {t1.hour:02d}:{t1.minute:02d}:00"
    except Exception:
        return "daily"


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------


def _python_exe() -> str:
    """Return the interpreter that has zsm installed.

    ``sys.executable`` is the venv / uv-tool python currently running the app,
    so the generated unit re-invokes the same environment that created it —
    correct for both an editable dev checkout and a ``uv tool install``. The
    old hardcoded ``INSTALL_DIR/.venv`` never existed (INSTALL_DIR is the
    ``app`` package dir, and uv-tool installs have no co-located ``.venv``).
    Mirrors the main service template, which also uses ``sys.executable``.
    """
    return sys.executable


def _working_dir() -> Path:
    """Working directory for anything that runs ``<python> -m app``.

    ``-m`` puts the working directory first on ``sys.path``. Until 2026 Q3
    this was ``INSTALL_DIR``, the ``app`` package itself, whose ``app/xml/``
    then hid the standard library's ``xml``: every task died at import with
    ``No module named 'xml.dom'`` (measured in the test container), and only
    ``restart``, a plain ``systemctl`` call, ever ran. The directory that
    *contains* ``app`` is already on ``sys.path`` (site-packages, or the repo
    root for an editable install), so putting it first changes nothing.
    Python 3.11's ``-P`` would also do it; zsm still supported 3.10 when this
    was written.
    """
    return INSTALL_DIR.parent


# How a refused path names these units (``service_control.unit_value``).
_TASK_UNIT = "a scheduled task's unit"


def _timer_unit_name(task: ScheduledTask, config=None) -> str:
    # Per instance (plans/44 step 6): `main` keeps `dayz-task-<id>`.
    return f"{task_unit_prefix(config)}{task.id}"


def generate_timer_content(task: ScheduledTask) -> str:
    """Generate systemd .timer unit content."""
    oncal = cron_to_oncalendar(task.cron)
    return (
        f"[Unit]\n"
        f"Description=DayZ Scheduled Task: {task.label} ({task.id})\n"
        f"\n"
        f"[Timer]\n"
        f"OnCalendar={oncal}\n"
        f"Persistent=true\n"
        f"AccuracySec=30s\n"
        f"\n"
        f"[Install]\n"
        f"WantedBy=timers.target\n"
    )


def _cli_args(task: ScheduledTask, config) -> str:
    """What a task unit passes to ``-m app``. It names its instance (plans/44
    step 5): without it the command loads whatever the default is when the
    timer fires, and `zsm instances use` would point one instance's
    scheduled update at another's config. The id is validated, so it is safe
    in an `ExecStart=` line."""
    ident = getattr(config, "instance_id", "") if config is not None else ""
    pin = f"--instance {ident} " if ident else ""
    return f"--yes {pin}{task_command(task)}"


def generate_service_content(task: ScheduledTask, config) -> str:
    """Generate systemd .service unit content for a scheduled task.

    Most tasks run as the invoking user through the zsm CLI (creating
    user-owned backups, wiping user-owned storage, etc.), so they carry an
    explicit ``User=`` in **system** scope.

    A "restart" task is special in **system** scope: restarting the
    system-level ``dayz-server.service`` is privileged, a non-root user can't do
    it, and ``sudo`` has no tty under a timer. Since restarting is *all* that
    task does, its oneshot runs as root (no ``User=``) and calls systemctl
    directly; systemd still launches the server under the main unit's own
    ``User=``.

    In **user** scope everything is simpler: the whole thing already runs as
    the owning user, so ``User=`` is dropped from every task, and the restart
    task is just a normal oneshot that runs ``systemctl --user restart`` — no
    root special case at all.

    The paths go through ``service_control.unit_value``, the panel unit's
    rule: raises ``UnitPathError`` for one systemd cannot take there.
    """
    import getpass

    user_mode = get_scope(config).user_mode

    if task.task_type == "restart":
        systemctl = unit_exec_word(
            "systemctl", shutil.which("systemctl") or "/usr/bin/systemctl", _TASK_UNIT
        )
        # Restart a running server and one systemd gave up on (failed), and
        # leave one the admin stopped (inactive) alone. A plain restart started
        # a server stopped for maintenance; try-restart left a crashed-out one
        # down for good, so a failed unit is restarted by design. A stop
        # Zedstead asked for reads inactive, not failed: clear_stop_failure.
        # ExecCondition exits 1 to skip, which is no failure; reset-failed
        # first, or a unit that hit the start limit refuses the restart.
        sc = "systemctl --user" if user_mode else "systemctl"
        word = f"{systemctl} --user" if user_mode else systemctl
        unit = server_unit(config)
        return (
            f"[Unit]\n"
            f"Description=DayZ Task: {task.label} ({task.id})\n"
            f"After=network.target\n"
            f"\n"
            f"[Service]\n"
            f"Type=oneshot\n"
            f"ExecCondition=/bin/sh -c '{sc} is-active --quiet {unit} || {sc} is-failed --quiet {unit}'\n"
            f"ExecStartPre=-{word} reset-failed {unit}\n"
            f"ExecStart={word} restart {unit}\n"
        )

    # Quoted only when the path needs it, so a normal unit's text is what it
    # was and an installed timer does not read as outdated (plans/68 B19).
    python_exe = unit_exec_word("The Python interpreter", _python_exe(), _TASK_UNIT)
    workdir = unit_value("The working directory", str(_working_dir()), _TASK_UNIT)

    # User= is invalid in the per-user manager (units already run as the owner).
    user_line = "" if user_mode else f"User={getpass.getuser()}\n"
    return (
        f"[Unit]\n"
        f"Description=DayZ Task: {task.label} ({task.id})\n"
        f"After=network.target\n"
        f"\n"
        f"[Service]\n"
        f"Type=oneshot\n"
        f"{user_line}"
        f"ExecStart={python_exe} -m app {_cli_args(task, config)}\n"
        f"WorkingDirectory={workdir}\n"
    )


def _installable_tasks(config, log: Callable[[str], None] | None = None) -> list[ScheduledTask]:
    """The enabled tasks a unit may be written for.

    Every task is validated again here, not only when it is added or edited:
    config.json can be edited by hand, and a task's id becomes a unit file
    name while its type reaches ``Description=`` (plans/68 INJ-9). A task that
    fails is skipped and named through *log*; the others are still installed.
    """
    log = log or _log.warning
    result: list[ScheduledTask] = []
    for task in load_tasks(config):
        if not task.enabled:
            continue
        errors = validate_task(task)
        if errors:
            log(f"Skipped scheduled task {task.id!r}: {'; '.join(errors)}")
            continue
        result.append(task)
    return result


def generate_timer_files(
    config, log: Callable[[str], None] | None = None
) -> list[tuple[str, str, str]]:
    """Generate timer + service content for all enabled, valid tasks.

    Returns list of (unit_name, timer_content, service_content).
    """
    result = []
    for task in _installable_tasks(config, log):
        name = _timer_unit_name(task, config)
        timer = generate_timer_content(task)
        service = generate_service_content(task, config)
        result.append((name, timer, service))
    return result


# ---------------------------------------------------------------------------
# What is actually installed (test pass 1, F42)
# ---------------------------------------------------------------------------
#
# "Next run" is computed from the cron alone, so a task whose timer was never
# installed — or was installed before an edit — showed a next run that would
# not happen. These read the unit files only: never `systemctl`, never sudo,
# because they run on a page load. Unit files are world-readable in both
# scopes.

TIMER_INSTALLED = "installed"
TIMER_OUTDATED = "outdated"
TIMER_MISSING = "missing"
TIMER_DISABLED = "disabled"

def _read_unit(path: Path) -> str | None:
    try:
        return path.read_text(encoding="utf-8")
    except OSError:
        return None


def _unit_names_in(unit_dir: Path, config) -> set[str]:
    """This instance's task units: another instance's never match its prefix."""
    if not unit_dir.is_dir():
        return set()
    prefix = task_unit_prefix(config)
    return {
        p.stem
        for suffix in (".timer", ".service")
        for p in unit_dir.glob(f"{prefix}*{suffix}")
    }


def _stale_unit_names(tasks: list[ScheduledTask], unit_dir: Path, config) -> list[str]:
    """Installed units no task wants: a disabled task's, and a deleted one's.

    Before plans/53 D nothing removed either — deleting a nightly restart
    task left its timer restarting the server.
    """
    present = _unit_names_in(unit_dir, config)
    wanted = {_timer_unit_name(t, config) for t in tasks if t.enabled}
    return sorted(present - wanted)


def _service_content_or_none(task: ScheduledTask, config) -> str | None:
    """The unit Install would write, or None where it would refuse the
    interpreter's path: a page load reports that as outdated, not a 500."""
    try:
        return generate_service_content(task, config)
    except UnitPathError:
        return None


def timer_install_state(
    config, unit_dir: Path | None = None
) -> tuple[dict[str, str], list[str]]:
    """``({task id: state}, orphans)`` for the Scheduler page.

    A state is ``installed`` (both units match what would be written now),
    ``outdated`` (anything else on disk, including a disabled task whose
    units are still there), ``missing`` or ``disabled``. Orphans are
    ``dayz-task-*`` units whose task no longer exists. ``({}, [])`` where
    there are no systemd units to read (Windows, no systemd): unknown, which
    must not read as "missing".
    """
    if IS_WINDOWS or (unit_dir is None and not has_systemd()):
        return {}, []
    unit_dir = unit_dir or get_scope(config).unit_dir
    tasks = load_tasks(config)
    states: dict[str, str] = {}
    for task in tasks:
        name = _timer_unit_name(task, config)
        timer = _read_unit(unit_dir / f"{name}.timer")
        service = _read_unit(unit_dir / f"{name}.service")
        if timer is None and service is None:
            states[task.id] = TIMER_MISSING if task.enabled else TIMER_DISABLED
        elif (
            task.enabled
            and timer == generate_timer_content(task)
            and service == _service_content_or_none(task, config)
        ):
            states[task.id] = TIMER_INSTALLED
        else:
            states[task.id] = TIMER_OUTDATED
    known = {_timer_unit_name(t, config) for t in tasks}
    orphans = sorted(_unit_names_in(unit_dir, config) - known)
    return states, orphans


# ---------------------------------------------------------------------------
# Windows Task Scheduler generation
# ---------------------------------------------------------------------------

_SCHTASKS_NS = "http://schemas.microsoft.com/windows/2004/02/mit/task"

# Indexed by cron's weekday and month numbers (croniter's Sunday is 0).
_XML_WEEKDAYS = ("Sunday", "Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday")
_XML_MONTHS = (
    "January", "February", "March", "April", "May", "June",
    "July", "August", "September", "October", "November", "December",
)
# Task Scheduler takes at most 48 triggers in one task.
_SCHTASKS_MAX_TRIGGERS = 48


class _NoSchtasksEquivalent(ValueError):
    """A schedule a Windows scheduled task cannot express."""


@dataclass
class _SchtasksPlan:
    """What a cron expression becomes in Task Scheduler's terms."""

    starts: list[int]  # minutes after midnight, one trigger each
    repeat: tuple[int, int] | None  # (every N minutes, until N minutes after the start)
    schedule: tuple  # ("day",) | ("week", days) | ("month", days, months) | ("nth", weeks, days, months)


def cron_schtasks_problem(expr: str) -> str | None:
    """Why *expr*, which a systemd timer can take, cannot become a Windows
    scheduled task, or None (plans/68 B22). ``validate_cron`` asks it on
    Windows only, so a Linux install keeps every schedule it has."""
    try:
        _schtasks_plan(expr)
    except _NoSchtasksEquivalent as e:
        return str(e)
    except ValueError:
        return "Windows scheduled tasks cannot express this schedule"
    return None


def _schtasks_plan(expr: str) -> _SchtasksPlan:
    """Read *expr* the way croniter does (``croniter.expand``: names, ``?``,
    ``7`` for Sunday, steps, ``L``, ``#``) and lay it out as triggers.

    Start times: one trigger when there is one, one trigger repeating at the
    interval when they are evenly spaced (``*/5``, ``0 */4``, ``*/15 9-17``),
    else a trigger per time, up to Task Scheduler's 48. Days: every day
    (``ScheduleByDay``), weekdays (``ScheduleByWeek``), days of the month with
    ``Last`` for ``L`` (``ScheduleByMonth``), or the nth/last weekday of the
    month (``ScheduleByMonthDayOfWeek``). A month restriction is kept: weekdays
    in some months become weeks 1-4 and Last, which together are every one.
    Raises ``_NoSchtasksEquivalent`` with the reason, ``ValueError`` for an
    expression croniter cannot read.
    """
    fields, nth = croniter.expand(expr)
    minutes = range(60) if fields[0][0] == "*" else fields[0]
    hours = range(24) if fields[1][0] == "*" else fields[1]
    times = sorted(h * 60 + m for h in hours for m in minutes)

    repeat = None
    gaps = {b - a for a, b in zip(times, times[1:])}
    if len(gaps) == 1:
        gap = gaps.pop()
        starts, repeat = times[:1], (gap, times[-1] - times[0])
    elif len(times) <= _SCHTASKS_MAX_TRIGGERS:
        starts = times
    else:
        raise _NoSchtasksEquivalent(
            f"Windows scheduled tasks take at most {_SCHTASKS_MAX_TRIGGERS} start "
            "times a day unless they are evenly spaced"
        )

    days, months, weekdays = fields[2], fields[3], fields[4]
    every_month = months[0] == "*"
    month_list = list(range(1, 13)) if every_month else list(months)
    if nth:
        # validate_cron refuses an nth or last weekday in a list.
        (weekday, ks), = nth.items()
        k = next(iter(ks))
        if k == 5:
            raise _NoSchtasksEquivalent(
                "Windows scheduled tasks count the weekdays of a month to the "
                "fourth and the last; there is no fifth"
            )
        schedule = ("nth", ["Last" if k == "l" else str(k)], [weekday], month_list)
    elif days[0] != "*":
        # With a weekday as well, validate_cron has made sure both are every
        # day, so the days alone say it.
        schedule = ("month", ["Last"] if days == ["l"] else [str(d) for d in days], month_list)
    elif weekdays[0] != "*":
        if every_month:
            schedule = ("week", list(weekdays))
        else:
            schedule = ("nth", ["1", "2", "3", "4", "Last"], list(weekdays), month_list)
    elif every_month:
        schedule = ("day",)
    else:
        schedule = ("month", [str(d) for d in range(1, 32)], month_list)
    return _SchtasksPlan(starts, repeat, schedule)


def _iso_duration(seconds: int) -> str:
    """``PT4H``, ``PT5M``, ``PT22H30M``: Task Scheduler's duration form."""
    h, rest = divmod(seconds, 3600)
    m, s = divmod(rest, 60)
    return "PT" + "".join(f"{v}{u}" for v, u in ((h, "H"), (m, "M"), (s, "S")) if v) if seconds else "PT0S"


def cron_to_schtasks_triggers(cron_expr: str) -> str:
    """Convert a cron expression to the schtasks XML ``<Triggers>`` block.

    See ``_schtasks_plan``. The element order is the one Task Scheduler's own
    export writes. A repetition runs until half an interval past the last
    start, so the last one is inside it whether Task Scheduler counts the end
    in or not, and the one after it is not. An expression it cannot take
    (``validate_cron`` refuses those on Windows) gets a daily trigger, so a
    caller that compares text never raises.
    """
    import xml.etree.ElementTree as ET

    try:
        plan = _schtasks_plan(cron_expr)
    except ValueError:
        return _cron_to_schtasks_fallback(cron_expr)

    def sub(parent, tag, text=None):
        el = ET.SubElement(parent, f"{{{_SCHTASKS_NS}}}{tag}")
        if text is not None:
            el.text = text
        return el

    triggers = ET.Element(f"{{{_SCHTASKS_NS}}}Triggers")
    for start in plan.starts:
        trigger = sub(triggers, "CalendarTrigger")
        if plan.repeat:
            every, span = plan.repeat
            rep = sub(trigger, "Repetition")
            sub(rep, "Interval", _iso_duration(every * 60))
            sub(rep, "Duration", _iso_duration(span * 60 + every * 30))
            sub(rep, "StopAtDurationEnd", "false")
        sub(trigger, "StartBoundary", f"2026-01-01T{start // 60:02d}:{start % 60:02d}:00")
        sub(trigger, "Enabled", "true")

        kind = plan.schedule[0]
        if kind == "day":
            sub(sub(trigger, "ScheduleByDay"), "DaysInterval", "1")
        elif kind == "week":
            week = sub(trigger, "ScheduleByWeek")
            dow = sub(week, "DaysOfWeek")
            for d in plan.schedule[1]:
                sub(dow, _XML_WEEKDAYS[d])
            sub(week, "WeeksInterval", "1")
        elif kind == "month":
            by_month = sub(trigger, "ScheduleByMonth")
            dom = sub(by_month, "DaysOfMonth")
            for d in plan.schedule[1]:
                sub(dom, "Day", d)
            months = sub(by_month, "Months")
            for m in plan.schedule[2]:
                sub(months, _XML_MONTHS[m - 1])
        else:
            by_dow = sub(trigger, "ScheduleByMonthDayOfWeek")
            weeks = sub(by_dow, "Weeks")
            for w in plan.schedule[1]:
                sub(weeks, "Week", w)
            dow = sub(by_dow, "DaysOfWeek")
            for d in plan.schedule[2]:
                sub(dow, _XML_WEEKDAYS[d])
            months = sub(by_dow, "Months")
            for m in plan.schedule[3]:
                sub(months, _XML_MONTHS[m - 1])

    ET.register_namespace("", _SCHTASKS_NS)
    return ET.tostring(triggers, encoding="unicode")


def _cron_to_schtasks_fallback(cron_expr: str) -> str:
    """Fallback for an expression no install path writes: a daily trigger."""
    import xml.etree.ElementTree as ET

    ET.register_namespace("", _SCHTASKS_NS)
    triggers = ET.Element(f"{{{_SCHTASKS_NS}}}Triggers")
    trigger = ET.SubElement(triggers, f"{{{_SCHTASKS_NS}}}CalendarTrigger")
    ET.SubElement(trigger, f"{{{_SCHTASKS_NS}}}StartBoundary").text = "2026-01-01T00:00:00"
    ET.SubElement(trigger, f"{{{_SCHTASKS_NS}}}Enabled").text = "true"
    by_day = ET.SubElement(trigger, f"{{{_SCHTASKS_NS}}}ScheduleByDay")
    ET.SubElement(by_day, f"{{{_SCHTASKS_NS}}}DaysInterval").text = "1"
    return ET.tostring(triggers, encoding="unicode")


def generate_schtasks_xml(task: ScheduledTask, config=None) -> str:
    """Generate a complete schtasks-compatible XML task definition."""
    import xml.etree.ElementTree as ET

    ET.register_namespace("", _SCHTASKS_NS)
    ns = _SCHTASKS_NS

    root = ET.Element(f"{{{ns}}}Task", version="1.2")

    # RegistrationInfo
    reg = ET.SubElement(root, f"{{{ns}}}RegistrationInfo")
    ET.SubElement(reg, f"{{{ns}}}Description").text = (
        f"DayZ Scheduled Task: {task.label} ({task.id})"
    )
    ET.SubElement(reg, f"{{{ns}}}URI").text = f"\\{_timer_unit_name(task, config)}"

    # Triggers
    root.append(_safe_fromstring(cron_to_schtasks_triggers(task.cron)))

    # Principals
    principals = ET.SubElement(root, f"{{{ns}}}Principals")
    principal = ET.SubElement(principals, f"{{{ns}}}Principal", id="Author")
    ET.SubElement(principal, f"{{{ns}}}LogonType").text = "InteractiveToken"
    ET.SubElement(principal, f"{{{ns}}}RunLevel").text = "LeastPrivilege"

    # Settings
    settings = ET.SubElement(root, f"{{{ns}}}Settings")
    for tag, val in [
        ("MultipleInstancesPolicy", "IgnoreNew"),
        ("DisallowStartIfOnBatteries", "false"),
        ("StopIfGoingOnBatteries", "false"),
        ("AllowHardTerminate", "true"),
        ("StartWhenAvailable", "true"),
        ("RunOnlyIfNetworkAvailable", "false"),
        ("AllowStartOnDemand", "true"),
        ("Enabled", "true"),
        ("Hidden", "false"),
        ("ExecutionTimeLimit", "PT1H"),
    ]:
        ET.SubElement(settings, f"{{{ns}}}{tag}").text = val

    # Actions
    actions = ET.SubElement(root, f"{{{ns}}}Actions", Context="Author")
    exe = ET.SubElement(actions, f"{{{ns}}}Exec")
    ET.SubElement(exe, f"{{{ns}}}Command").text = _python_exe()
    ET.SubElement(exe, f"{{{ns}}}Arguments").text = f"-m app {_cli_args(task, config)}"
    ET.SubElement(exe, f"{{{ns}}}WorkingDirectory").text = str(_working_dir())

    return '<?xml version="1.0" encoding="UTF-16"?>\n' + ET.tostring(
        root, encoding="unicode",
    )


def generate_schtasks_files(
    config, log: Callable[[str], None] | None = None
) -> list[tuple[str, str]]:
    """Generate schtasks XML content for all enabled, valid tasks.

    Returns list of (task_name, xml_content).
    """
    result = []
    for task in _installable_tasks(config, log):
        name = _timer_unit_name(task, config)
        xml = generate_schtasks_xml(task, config)
        result.append((name, xml))
    return result


# ---------------------------------------------------------------------------
# Install / remove (platform-dispatching)
# ---------------------------------------------------------------------------


def install_timers(config, dest_dir: Path | None = None) -> list[str]:
    """Write timer/service files (sync, for tests with dest_dir).

    Linux: writes to the scope's unit dir (system scope needs sudo).
    Windows: writes XML to dest_dir or config.paths.schtasks_dir, registers via schtasks.
    The CLI and the web call ``install_timers_async``.
    """
    if IS_WINDOWS:
        dest = dest_dir or config.paths.schtasks_dir
        dest.mkdir(parents=True, exist_ok=True)
        installed: list[str] = []
        for name, xml_content in generate_schtasks_files(config):
            xml_path = dest / f"{name}.xml"
            xml_path.write_text(xml_content, encoding="utf-16")
            if not dest_dir:
                subprocess.run(
                    ["schtasks", "/Create", "/XML", str(xml_path), "/TN", name, "/F"],
                    check=False,
                )
            installed.append(name)
        return installed

    scope = get_scope(config)
    dest = dest_dir or scope.unit_dir
    dest.mkdir(parents=True, exist_ok=True)
    installed: list[str] = []
    env = scope.subprocess_env()

    for name, timer_content, service_content in generate_timer_files(config):
        timer_path = dest / f"{name}.timer"
        service_path = dest / f"{name}.service"
        timer_path.write_text(timer_content, encoding="utf-8")
        service_path.write_text(service_content, encoding="utf-8")
        installed.append(name)

    # Installing brings the timers in line with the task list, so a disabled
    # or deleted task's timer goes too (plans/53 D).
    stale = _stale_unit_names(load_tasks(config), dest, config)
    _remove_units(scope, env, dest, stale, dest_dir is None)

    if not dest_dir and (installed or stale):
        subprocess.run(scope.systemctl_argv("daemon-reload"), check=False, env=env)
        for name in installed:
            subprocess.run(scope.systemctl_argv("enable", f"{name}.timer"), check=False, env=env)
            subprocess.run(scope.systemctl_argv("start", f"{name}.timer"), check=False, env=env)

    return installed


async def install_timers_async(config, log: Callable[[str], None] | None = None) -> list[str]:
    """Write timer/service files and register them. Returns installed names.

    Linux: writes via sudo tee + systemctl.
    Windows: writes XML + schtasks /Create.
    A task that no longer validates is skipped and named through *log*.
    """
    import asyncio

    if IS_WINDOWS:
        xml_dir = config.paths.schtasks_dir
        xml_dir.mkdir(parents=True, exist_ok=True)
        installed: list[str] = []
        for name, xml_content in generate_schtasks_files(config, log):
            xml_path = xml_dir / f"{name}.xml"
            xml_path.write_text(xml_content, encoding="utf-16")
            proc = await asyncio.create_subprocess_exec(
                "schtasks", "/Create", "/XML", str(xml_path), "/TN", name, "/F",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            _, stderr = await proc.communicate()
            if proc.returncode != 0:
                raise RuntimeError(
                    f"Failed to create scheduled task {name}: "
                    f"{stderr.decode().strip()}"
                )
            installed.append(name)
        return installed

    if not has_systemd():
        raise RuntimeError(
            "systemd is not available — cannot install timers. Schedule restarts "
            "via cron or your container / supervisor instead."
        )

    scope = get_scope(config)
    env = scope.subprocess_env()
    if scope.user_mode:
        scope.unit_dir.mkdir(parents=True, exist_ok=True)

    installed: list[str] = []
    for name, timer_content, service_content in generate_timer_files(config, log):
        for suffix, content in [(".timer", timer_content), (".service", service_content)]:
            if scope.user_mode:
                # No sudo: write the unit file directly into the user unit dir.
                (scope.unit_dir / f"{name}{suffix}").write_text(content, encoding="utf-8")
                continue
            path = str(scope.unit_dir / f"{name}{suffix}")
            proc = await asyncio.create_subprocess_exec(
                *sudo_prefix(), "tee", path,
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.DEVNULL,
            )
            proc.stdin.write(content.encode())
            await proc.stdin.drain()
            proc.stdin.close()
            await proc.wait()
            if proc.returncode != 0:
                raise RuntimeError(f"Failed to write {path} (exit {proc.returncode})")
        installed.append(name)

    # Installing brings the timers in line with the task list, so a disabled
    # or deleted task's timer goes too (plans/53 D).
    stale = _stale_unit_names(load_tasks(config), scope.unit_dir, config)
    await _remove_units_async(scope, env, stale)
    # What the click changed in systemd, one line each: the job's log was
    # empty and the drawer said "No log output yet."
    if log:
        for name in installed:
            log(f"Installed {name}.timer")
        for name in stale:
            log(f"Removed {name}.timer, its task is gone or turned off")
        if not installed and not stale:
            log("No timers to install: no enabled scheduled task.")

    if installed or stale:
        proc = await asyncio.create_subprocess_exec(
            *scope.systemctl_argv("daemon-reload"), env=env
        )
        await proc.wait()
        if proc.returncode != 0:
            raise RuntimeError(f"systemctl daemon-reload failed (exit {proc.returncode})")
        for name in installed:
            proc = await asyncio.create_subprocess_exec(
                *scope.systemctl_argv("enable", f"{name}.timer"), env=env,
            )
            await proc.wait()
            if proc.returncode != 0:
                raise RuntimeError(f"systemctl enable {name}.timer failed (exit {proc.returncode})")
            proc = await asyncio.create_subprocess_exec(
                *scope.systemctl_argv("start", f"{name}.timer"), env=env,
            )
            await proc.wait()
            if proc.returncode != 0:
                raise RuntimeError(f"systemctl start {name}.timer failed (exit {proc.returncode})")

    return installed


def remove_timers(config, dest_dir: Path | None = None) -> list[str]:
    """Remove timer/service files (sync, for tests with dest_dir).

    Linux: stops/disables systemd timers, deletes unit files.
    Windows: deletes schtasks entries and XML files.
    The CLI and the web call ``remove_timers_async``.
    """
    if IS_WINDOWS:
        dest = dest_dir or config.paths.schtasks_dir
        removed: list[str] = []
        for task in load_tasks(config):
            name = _timer_unit_name(task, config)
            if not dest_dir:
                subprocess.run(
                    ["schtasks", "/Delete", "/TN", name, "/F"],
                    check=False,
                )
            xml_path = dest / f"{name}.xml"
            if xml_path.exists():
                xml_path.unlink()
            removed.append(name)
        return removed

    scope = get_scope(config)
    dest = dest_dir or scope.unit_dir
    env = scope.subprocess_env()
    # Every task's units, plus leftovers from deleted tasks.
    names = [_timer_unit_name(t, config) for t in load_tasks(config)]
    names += sorted(_unit_names_in(dest, config) - set(names))
    _remove_units(scope, env, dest, names, dest_dir is None)

    if not dest_dir and names:
        subprocess.run(scope.systemctl_argv("daemon-reload"), check=False, env=env)

    return names


def _remove_units(scope, env, dest: Path, names: list[str], live: bool) -> None:
    """Stop, disable and delete *names*' units (sync path; ``live`` False is
    a test's ``dest_dir``, where there is no systemd to tell)."""
    for name in names:
        if live:
            subprocess.run(scope.systemctl_argv("stop", f"{name}.timer"), check=False, env=env)
            subprocess.run(scope.systemctl_argv("disable", f"{name}.timer"), check=False, env=env)
        for suffix in (".timer", ".service"):
            (dest / f"{name}{suffix}").unlink(missing_ok=True)


async def remove_timers_async(config) -> list[str]:
    """Remove scheduled tasks/timers asynchronously.

    Linux: stops/disables systemd timers via sudo.
    Windows: deletes schtasks entries and XML files.
    """
    import asyncio

    if IS_WINDOWS:
        xml_dir = config.paths.schtasks_dir
        removed: list[str] = []
        for task in load_tasks(config):
            name = _timer_unit_name(task, config)
            proc = await asyncio.create_subprocess_exec(
                "schtasks", "/Delete", "/TN", name, "/F",
                stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.DEVNULL,
            )
            await proc.wait()
            xml_path = xml_dir / f"{name}.xml"
            if xml_path.exists():
                xml_path.unlink()
            removed.append(name)
        return removed

    if not has_systemd():
        # No systemd means no timers were ever installed — nothing to remove.
        return []

    scope = get_scope(config)
    env = scope.subprocess_env()
    # Every task's units, plus leftovers from deleted tasks.
    removed = [_timer_unit_name(t, config) for t in load_tasks(config)]
    removed += sorted(_unit_names_in(scope.unit_dir, config) - set(removed))
    await _remove_units_async(scope, env, removed)

    if removed:
        proc = await asyncio.create_subprocess_exec(
            *scope.systemctl_argv("daemon-reload"), env=env
        )
        await proc.wait()
        if proc.returncode != 0:
            raise RuntimeError(f"systemctl daemon-reload failed (exit {proc.returncode})")

    return removed


async def _remove_units_async(scope, env, names: list[str]) -> None:
    """Stop, disable and delete *names*' units. The caller daemon-reloads."""
    import asyncio

    for name in names:
        # stop/disable are best-effort — timer may already be stopped/disabled.
        proc = await asyncio.create_subprocess_exec(
            *scope.systemctl_argv("stop", f"{name}.timer"), env=env,
            stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.DEVNULL,
        )
        await proc.wait()
        proc = await asyncio.create_subprocess_exec(
            *scope.systemctl_argv("disable", f"{name}.timer"), env=env,
            stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.DEVNULL,
        )
        await proc.wait()
        for suffix in (".timer", ".service"):
            if scope.user_mode:
                # No sudo: delete the unit file directly from the user dir.
                (scope.unit_dir / f"{name}{suffix}").unlink(missing_ok=True)
                continue
            path = str(scope.unit_dir / f"{name}{suffix}")
            proc = await asyncio.create_subprocess_exec(*sudo_prefix(), "rm", "-f", path)
            await proc.wait()
            if proc.returncode != 0:
                raise RuntimeError(
                    f"Failed to remove {path} (exit {proc.returncode})"
                )


# ---------------------------------------------------------------------------
# Preset schedules
# ---------------------------------------------------------------------------

SCHEDULE_PRESETS: dict[str, dict] = {
    "restart_4h": {
        "id": "restart_4h",
        "task_type": "restart",
        "cron": "0 */4 * * *",
        "label": "Restart every 4 hours",
    },
    "restart_6h": {
        "id": "restart_6h",
        "task_type": "restart",
        "cron": "0 */6 * * *",
        "label": "Restart every 6 hours",
    },
    "backup_6h": {
        "id": "backup_6h",
        "task_type": "backup",
        "cron": "0 */6 * * *",
        "label": "Backup every 6 hours",
    },
    "backup_daily": {
        "id": "backup_daily",
        "task_type": "backup",
        "cron": "0 4 * * *",
        "label": "Backup daily at 04:00",
    },
    "mod_check_daily": {
        "id": "mod_check_daily",
        "task_type": "mod_check",
        "cron": "0 3 * * *",
        "label": "Check mods daily at 03:00",
    },
    "wipe_weekly": {
        "id": "wipe_weekly",
        "task_type": "wipe",
        "cron": "0 4 * * 1",
        "label": "Wipe weekly on Monday at 04:00",
    },
}


def preset_display_label(key: str) -> str:
    """Human-readable label for a preset: 'Restart every 4 hours (0 */4 * * *)'."""
    p = SCHEDULE_PRESETS[key]
    return f"{p['label']} ({p['cron']})"
