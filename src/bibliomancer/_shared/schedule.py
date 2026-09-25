"""Weekly / monthly runs registered with the operating system's scheduler.

    Windows  Task Scheduler, from an XML definition (schtasks flags take dates
             in the locale's format; XML is ISO everywhere). Runs "only when the
             user is logged on" and never elevated: an elevated task does not
             see mapped drives such as Google Drive's H:.
    macOS    a launchd agent in ~/Library/LaunchAgents.
    Linux    a systemd user timer (Persistent=true, so a missed run happens at
             the next boot); crontab when systemd is not available.

The first scheduled run is one period after now, because the run that
registers the schedule happens immediately anyway. `argv` is the complete
command (see paths.launcher_command + the app's `scheduled` subcommand, which
writes its own log - no shell redirection needed here).
"""

from __future__ import annotations

import datetime as dt
import os
import plistlib
import re
import shutil
import subprocess
import tempfile

from .paths import IS_MAC, IS_WIN, NO_WINDOW, run_quiet

WEEKDAYS = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]
MONTHS = ("January February March April May June July August September "
          "October November December").split()


def describe(mode: str, now: dt.datetime | None = None) -> str:
    """The hint under the Repeat buttons."""
    now = now or dt.datetime.now()
    return {"oneshot": "Runs once, now. An existing schedule is left as it is.",
            "weekly": f"Runs now, then every {WEEKDAYS[now.weekday()]} at {now:%H:%M}.",
            "monthly": f"Runs now, then on day {min(now.day, 28)} of every month at {now:%H:%M}."
            }[mode]


def _first_run(mode: str, now: dt.datetime) -> dt.datetime:
    if mode == "weekly":
        return (now + dt.timedelta(days=7)).replace(second=0, microsecond=0)
    day_n = min(now.day, 28)                      # fires every month, February included
    month, year = now.month % 12 + 1, now.year + (now.month == 12)
    return now.replace(year=year, month=month, day=day_n, second=0, microsecond=0)


def _slug(task_name: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", task_name.lower()).strip("-")


# ------------------------------------------------------------------ Windows --
def task_xml(mode: str, argv: list[str], workdir: str, description: str,
             now: dt.datetime) -> str:
    start = _first_run(mode, now)
    if mode == "weekly":
        sched = (f"<ScheduleByWeek><DaysOfWeek><{WEEKDAYS[start.weekday()]} /></DaysOfWeek>"
                 f"<WeeksInterval>1</WeeksInterval></ScheduleByWeek>")
    else:
        sched = (f"<ScheduleByMonth><DaysOfMonth><Day>{start.day}</Day></DaysOfMonth><Months>"
                 + "".join(f"<{m} />" for m in MONTHS) + "</Months></ScheduleByMonth>")
    esc = lambda s: s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")  # noqa: E731
    args = subprocess.list2cmdline(argv[1:])
    return f"""<?xml version="1.0" encoding="UTF-16"?>
<Task version="1.2" xmlns="http://schemas.microsoft.com/windows/2004/02/mit/task">
  <RegistrationInfo><Description>{esc(description)}</Description></RegistrationInfo>
  <Triggers><CalendarTrigger>
    <StartBoundary>{start.strftime("%Y-%m-%dT%H:%M:00")}</StartBoundary>
    <Enabled>true</Enabled>{sched}
  </CalendarTrigger></Triggers>
  <Principals><Principal id="Author">
    <LogonType>InteractiveToken</LogonType><RunLevel>LeastPrivilege</RunLevel>
  </Principal></Principals>
  <Settings>
    <MultipleInstancesPolicy>IgnoreNew</MultipleInstancesPolicy>
    <DisallowStartIfOnBatteries>false</DisallowStartIfOnBatteries>
    <StopIfGoingOnBatteries>false</StopIfGoingOnBatteries>
    <StartWhenAvailable>true</StartWhenAvailable>
    <ExecutionTimeLimit>PT12H</ExecutionTimeLimit>
    <Enabled>true</Enabled>
  </Settings>
  <Actions Context="Author"><Exec>
    <Command>{esc(argv[0])}</Command><Arguments>{esc(args)}</Arguments><WorkingDirectory>{esc(workdir)}</WorkingDirectory>
  </Exec></Actions>
</Task>"""


def _win_register(task_name, mode, argv, workdir, description):
    xml = task_xml(mode, argv, workdir, description, dt.datetime.now())
    path = os.path.join(tempfile.gettempdir(), f"{_slug(task_name)}_task.xml")
    with open(path, "w", encoding="utf-16") as fh:
        fh.write(xml)
    p = run_quiet(["schtasks", "/Create", "/F", "/TN", task_name, "/XML", path], console=True)
    try:
        os.remove(path)
    except OSError:
        pass
    return p.returncode == 0, (p.stdout + p.stderr).strip()


def _win_status(task_name):
    ps = ("$t = Get-ScheduledTask -TaskName '%s' -ErrorAction SilentlyContinue; "
          "if (-not $t) { 'none' } else { "
          "$x = [xml](Export-ScheduledTask -TaskName '%s'); "
          "$k = if ($x.Task.Triggers.CalendarTrigger.ScheduleByWeek) {'weekly'} "
          "elseif ($x.Task.Triggers.CalendarTrigger.ScheduleByMonth) {'monthly'} else {'custom'}; "
          "$i = Get-ScheduledTaskInfo -TaskName '%s'; "
          "'{0}|{1}' -f $k, $i.NextRunTime.ToString('ddd dd MMM HH:mm') }"
          ) % (task_name, task_name, task_name)
    try:
        p = subprocess.run(["powershell", "-NoProfile", "-Command", ps], capture_output=True,
                           text=True, timeout=30, creationflags=NO_WINDOW)
        return p.stdout.strip() or "none"
    except Exception:
        return "unknown"


# -------------------------------------------------------------------- macOS --
def _mac_paths(task_name):
    label = f"local.{_slug(task_name)}"
    return label, os.path.expanduser(f"~/Library/LaunchAgents/{label}.plist")


def _mac_register(task_name, mode, argv, workdir, description):
    label, path = _mac_paths(task_name)
    start = _first_run(mode, dt.datetime.now())
    cal = {"Hour": start.hour, "Minute": start.minute}
    if mode == "weekly":
        cal["Weekday"] = (start.weekday() + 1) % 7          # launchd: 0 = Sunday
    else:
        cal["Day"] = start.day
    plist = {"Label": label, "ProgramArguments": argv, "WorkingDirectory": workdir,
             "StartCalendarInterval": cal, "RunAtLoad": False,
             "Comment": description, "ProcessType": "Background"}
    os.makedirs(os.path.dirname(path), exist_ok=True)
    uid = os.getuid()
    run_quiet(["launchctl", "bootout", f"gui/{uid}", path])
    with open(path, "wb") as fh:
        plistlib.dump(plist, fh)
    p = run_quiet(["launchctl", "bootstrap", f"gui/{uid}", path])
    return p.returncode == 0, (p.stdout + p.stderr).strip() or f"registered {path}"


def _next_from(cal: dict, now: dt.datetime) -> dt.datetime:
    t = now.replace(hour=cal.get("Hour", 0), minute=cal.get("Minute", 0), second=0, microsecond=0)
    if "Weekday" in cal:
        wd = (cal["Weekday"] - 1) % 7                        # back to Monday = 0
        t += dt.timedelta(days=(wd - t.weekday()) % 7)
        return t if t > now else t + dt.timedelta(days=7)
    day = cal.get("Day", 1)
    for k in range(3):
        m, y = (now.month - 1 + k) % 12 + 1, now.year + (now.month - 1 + k) // 12
        cand = t.replace(year=y, month=m, day=day)
        if cand > now:
            return cand
    return t


def _mac_status(task_name):
    _label, path = _mac_paths(task_name)
    if not os.path.exists(path):
        return "none"
    try:
        with open(path, "rb") as fh:
            cal = plistlib.load(fh).get("StartCalendarInterval", {})
        kind = "weekly" if "Weekday" in cal else "monthly"
        return f"{kind}|{_next_from(cal, dt.datetime.now()):%a %d %b %H:%M}"
    except Exception:
        return "custom|?"


# -------------------------------------------------------------------- Linux --
def _unit(task_name):
    base = os.path.join(os.environ.get("XDG_CONFIG_HOME") or os.path.expanduser("~/.config"),
                        "systemd", "user")
    name = _slug(task_name)
    return name, os.path.join(base, f"{name}.service"), os.path.join(base, f"{name}.timer")


def _quote(a: str) -> str:
    return '"' + a.replace("\\", "\\\\").replace('"', '\\"') + '"' if re.search(r"\s|\"", a) else a


def _linux_register(task_name, mode, argv, workdir, description):
    start = _first_run(mode, dt.datetime.now())
    oncal = (f"{start:%a} *-*-* {start:%H:%M}:00" if mode == "weekly"
             else f"*-*-{start.day:02d} {start:%H:%M}:00")
    if shutil.which("systemctl"):
        name, svc, tmr = _unit(task_name)
        os.makedirs(os.path.dirname(svc), exist_ok=True)
        with open(svc, "w", encoding="utf-8") as fh:
            fh.write(f"[Unit]\nDescription={description}\n\n[Service]\nType=oneshot\n"
                     f"WorkingDirectory={workdir}\nExecStart={' '.join(_quote(a) for a in argv)}\n")
        with open(tmr, "w", encoding="utf-8") as fh:
            fh.write(f"[Unit]\nDescription={description}\n\n[Timer]\nOnCalendar={oncal}\n"
                     f"Persistent=true\n\n[Install]\nWantedBy=timers.target\n")
        run_quiet(["systemctl", "--user", "daemon-reload"])
        p = run_quiet(["systemctl", "--user", "enable", "--now", f"{name}.timer"])
        return p.returncode == 0, (p.stdout + p.stderr).strip() or f"timer {name} enabled"
    # cron fallback
    tag = f"# {_slug(task_name)}"
    minute, hour = start.minute, start.hour
    when = (f"{minute} {hour} * * {(start.weekday() + 1) % 7}" if mode == "weekly"
            else f"{minute} {hour} {start.day} * *")
    cur = run_quiet(["crontab", "-l"]).stdout.splitlines()
    cur = [ln for ln in cur if not ln.endswith(tag)]
    cur.append(f"{when} cd {_quote(workdir)} && {' '.join(_quote(a) for a in argv)} {tag}")
    p = subprocess.run(["crontab", "-"], input="\n".join(cur) + "\n", text=True,
                       capture_output=True)
    return p.returncode == 0, (p.stdout + p.stderr).strip() or "crontab entry added"


def _linux_status(task_name):
    name, _svc, tmr = _unit(task_name)
    if os.path.exists(tmr):
        try:
            oncal = re.search(r"OnCalendar=(.+)", open(tmr, encoding="utf-8").read()).group(1)
            kind = "weekly" if oncal[:1].isalpha() else "monthly"
            p = run_quiet(["systemctl", "--user", "show", f"{name}.timer", "-p",
                           "NextElapseUSecRealtime", "--value"], timeout=10)
            nxt = p.stdout.strip() or "?"
            return f"{kind}|{nxt}"
        except Exception:
            return "custom|?"
    try:
        tag = f"# {_slug(task_name)}"
        line = next((ln for ln in run_quiet(["crontab", "-l"]).stdout.splitlines()
                     if ln.endswith(tag)), None)
    except OSError:
        line = None
    if not line:
        return "none"
    f = line.split()
    return f"{'weekly' if f[4] != '*' else 'monthly'}|{f[1]}:{int(f[0]):02d}"


def _linux_remove(task_name):
    name, svc, tmr = _unit(task_name)
    msgs = []
    if os.path.exists(tmr):
        run_quiet(["systemctl", "--user", "disable", "--now", f"{name}.timer"])
        for p in (svc, tmr):
            os.remove(p)
        run_quiet(["systemctl", "--user", "daemon-reload"])
        msgs.append(f"timer {name} removed")
    if shutil.which("crontab"):
        tag = f"# {_slug(task_name)}"
        cur = run_quiet(["crontab", "-l"]).stdout.splitlines()
        if any(ln.endswith(tag) for ln in cur):
            subprocess.run(["crontab", "-"], input="\n".join(ln for ln in cur if not ln.endswith(tag))
                           + "\n", text=True, capture_output=True)
            msgs.append("crontab entry removed")
    return bool(msgs), "; ".join(msgs) or "no schedule found"


# ------------------------------------------------------------------- public --
def register(task_name: str, mode: str, argv: list[str], workdir: str,
             description: str) -> tuple[bool, str]:
    """Create or replace the weekly/monthly run. Returns (ok, message)."""
    try:
        if IS_WIN:
            return _win_register(task_name, mode, argv, workdir, description)
        if IS_MAC:
            return _mac_register(task_name, mode, argv, workdir, description)
        return _linux_register(task_name, mode, argv, workdir, description)
    except Exception as exc:
        return False, f"{type(exc).__name__}: {exc}"


def remove(task_name: str) -> tuple[bool, str]:
    try:
        if IS_WIN:
            p = run_quiet(["schtasks", "/Delete", "/F", "/TN", task_name], console=True)
            return p.returncode == 0, (p.stdout + p.stderr).strip()
        if IS_MAC:
            _label, path = _mac_paths(task_name)
            if not os.path.exists(path):
                return False, "no schedule found"
            run_quiet(["launchctl", "bootout", f"gui/{os.getuid()}", path])
            os.remove(path)
            return True, f"removed {path}"
        return _linux_remove(task_name)
    except Exception as exc:
        return False, f"{type(exc).__name__}: {exc}"


def status(task_name: str) -> str:
    """'none', 'unknown', or '<weekly|monthly|custom>|<next run>'."""
    try:
        if IS_WIN:
            return _win_status(task_name)
        if IS_MAC:
            return _mac_status(task_name)
        return _linux_status(task_name)
    except Exception:
        return "unknown"
