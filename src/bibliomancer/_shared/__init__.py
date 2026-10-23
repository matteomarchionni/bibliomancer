"""Code shared, byte for byte, by BiblioMancer and PhotoMancer.

This folder exists in BOTH apps (src/bibliomancer/_shared and src/photomancer/_shared)
and must stay identical: each app is independent and installs on its own, so
the common code is copied rather than imported from a third package.

Rules
  * Nothing app-specific in here: names, colours, pages, commands and paths are
    passed in by the app (see app.py: AppSpec).
  * Change it in one app, then run `python tools/sync_shared.py push` to copy it
    to the other one; commit both with the same message ("shared <version>: ...").
  * VERSION below and the CHECKSUM file change together; a test in each app
    fails if the folder was edited without `sync_shared.py stamp`.
"""

VERSION = "1.9"
