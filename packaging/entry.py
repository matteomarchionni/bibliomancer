"""Entry point of the frozen (PyInstaller) app: no arguments = the panel,
otherwise the command line - exactly like BiblioMancer.pyw in a checkout."""
import multiprocessing
import sys

from bibliomancer.cli import main

if __name__ == "__main__":
    # A frozen app ignores PYTHONIOENCODING/PYTHONUTF8, so output piped to the
    # panel would use the ANSI code page (cp1252) and the first character it
    # lacks (e.g. LM Studio's spinner) would crash the job. UTF-8, never fatal.
    for stream in (sys.stdout, sys.stderr):
        if stream is not None and hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")
    # worker processes (the tagger's photo preparation pool) re-start this
    # executable; freeze_support() turns them into workers instead of new apps
    multiprocessing.freeze_support()
    sys.exit(main())
