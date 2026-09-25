"""Entry point of the frozen (PyInstaller) app: no arguments = the panel,
otherwise the command line - exactly like BiblioMancer.pyw in a checkout."""
import multiprocessing
import sys

from bibliomancer.cli import main

if __name__ == "__main__":
    # worker processes (the tagger's photo preparation pool) re-start this
    # executable; freeze_support() turns them into workers instead of new apps
    multiprocessing.freeze_support()
    sys.exit(main())
