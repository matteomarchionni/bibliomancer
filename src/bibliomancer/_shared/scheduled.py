"""The `scheduled` subcommand of both apps: what the OS scheduler runs.

It replaces the old run_weekly.cmd / run_scheduled.cmd with the same Python on
every system. It appends to <logs>/run_scheduled.log

    2026-09-25 03:00:00 scheduled run started (<detail>)
    ...the job's own output...
    2026-09-25 03:41:10 finished with exit code 0

and the panel follows that file live when it opens during a scheduled run
(see app.py: watcher). The job runs as a child with -u (unbuffered), so
nothing is lost if the PC restarts mid-run, and with no console window.
"""

from __future__ import annotations

import datetime as dt
import os
import subprocess

from .paths import popen_kwargs

LOG_NAME = "run_scheduled.log"
START_MARK = "scheduled run started"
END_MARK = "finished with exit code"


def run_logged(child_argv: list[str], log_dir: str, env: dict, detail: str = "") -> int:
    os.makedirs(log_dir, exist_ok=True)
    path = os.path.join(log_dir, LOG_NAME)
    stamp = lambda: dt.datetime.now().strftime("%Y-%m-%d %H:%M:%S")  # noqa: E731
    with open(path, "a", encoding="utf-8", buffering=1) as log:
        log.write(f"\n{stamp()} {START_MARK} ({detail})\n")
        log.flush()
        try:
            rc = subprocess.call(child_argv, stdout=log, stderr=subprocess.STDOUT, env=env,
                                 **popen_kwargs())
        except Exception as exc:
            log.write(f"ERROR could not start the job: {type(exc).__name__}: {exc}\n")
            rc = 1
        log.write(f"{stamp()} {END_MARK} {rc}\n")
    return rc
