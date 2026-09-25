"""Command line: python -m bibliomancer <command> [options]   (no command = the panel)

    update     the whole pipeline: model, scouts, new papers, dedup, index, export
               [--limit N] [--max-minutes M] [--retry-failed] [--no-vision]
               [--skip-supplementary] [--system-prompt-file F]
    rebuild    back up, clear, re-read the whole library (same options)
    post       dedup + search index + matrix + export only
    plan       what an update would process (changes nothing)
    check      environment check: folders, LM Studio, model, embeddings
    report     statistics of the library (Markdown + CSV in Research/)
    scouts     re-read the scout briefs            dedup   report/remove duplicates
    index      update the semantic search index    export  export + backup now
    search     "question" [--semantic]             research --query Q --top N [--question Q]
    reset      back up and clear (used by rebuild)
    scheduled  <command ...>   run a command with its output appended to
               logs/run_scheduled.log (what the weekly/monthly task runs)
    unschedule remove the weekly / monthly task
    shortcuts  [--desktop] [--menu] [--remove]   desktop icon / Start-menu entry
    setup      first-run wizard (folders, LM Studio check, shortcuts)
    gui        the control panel (default)
"""

from __future__ import annotations

import importlib
import sys

# command -> (module, argv prefix given to that module's main)
MODULES = {
    "update": ("pipeline", ["update"]), "rebuild": ("pipeline", ["rebuild"]),
    "post": ("pipeline", ["post"]), "plan": ("ingest", ["--dry-run"]),
    "check": ("check", []), "report": ("report", []), "scouts": ("scouts", ["--show"]),
    "dedup": ("dedup", []), "index": ("embed", []), "export": ("export", []),
    "search": ("search", []), "research": ("research", []), "reset": ("reset", []),
    "ingest": ("ingest", []), "matrix": ("matrix", []), "projects": ("projects", []),
}


def _call_module(name: str, args: list[str]) -> int:
    mod = importlib.import_module(f"bibliomancer.{name}")
    old = sys.argv
    sys.argv = [f"bibliomancer {name}", *args]
    try:
        rc = mod.main()                       # every main() parses sys.argv[1:]
    except SystemExit as exc:
        rc = exc.code if isinstance(exc.code, int) else (0 if exc.code is None else 1)
    finally:
        sys.argv = old
    return int(rc or 0)


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    if not argv or argv[0] == "gui":
        from .gui import main as gui_main
        gui_main()
        return 0
    cmd, rest = argv[0], argv[1:]
    if cmd in ("-h", "--help", "help"):
        print(__doc__)
        return 0
    if cmd == "_module":                          # frozen app: pipeline steps as subprocesses
        return _call_module(rest[0], rest[1:])
    if cmd == "scheduled":
        from . import core as kb
        from ._shared import paths
        from ._shared.scheduled import run_logged
        from .config import PKG_FILE
        child = [*paths.self_command("bibliomancer", PKG_FILE), *rest]
        return run_logged(child, kb.LOG_DIR, paths.child_env(PKG_FILE), " ".join(rest))
    if cmd == "shortcuts":
        from .gui import make_shortcuts
        return make_shortcuts(desktop="--desktop" in rest, menu="--menu" in rest or
                              "--desktop" not in rest, remove="--remove" in rest)
    if cmd == "setup":
        from .gui import run_wizard
        return run_wizard()
    if cmd == "unschedule":                      # also run by the uninstaller
        from ._shared import schedule
        from .gui import SPEC
        ok, msg = schedule.remove(SPEC.task_name)
        print(msg)
        return 0
    if cmd == "logdir":
        from . import core as kb
        print(kb.LOG_DIR)
        return 0
    if cmd not in MODULES:
        print(f"unknown command {cmd!r}\n{__doc__}")
        return 2
    name, prefix = MODULES[cmd]
    return _call_module(name, [*prefix, *rest])


if __name__ == "__main__":
    sys.exit(main())
