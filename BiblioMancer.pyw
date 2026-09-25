"""BiblioMancer launcher for a source checkout (no installation needed).

    pythonw BiblioMancer.pyw                 the control panel (shortcuts point here)
    python  BiblioMancer.pyw <command> ...   the command line, e.g. `update`, `check`
    pythonw BiblioMancer.pyw scheduled ...   what the weekly / monthly task runs

It only puts src/ on the import path; installed copies use `bibliomancer` /
`python -m bibliomancer` instead, frozen builds their own executable.
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "src"))
from bibliomancer.cli import main  # noqa: E402

sys.exit(main())
