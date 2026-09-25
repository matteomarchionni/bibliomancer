"""
rebuild_matrix.py - Regenerate synthesis_matrix.md from manifest.db.

The matrix is a derived view, never an append log. Run this after editing notes,
deleting rows, or if the file is ever damaged by a sync conflict.

    python rebuild_matrix.py
"""

from __future__ import annotations

import os

from . import core as kb


def main() -> int:
    if not os.path.exists(kb.DB_PATH):
        print("No manifest.db - nothing to rebuild.")
        return 1
    conn = kb.connect()
    try:
        n = kb.rebuild_matrix(conn)
        print(f"Wrote {kb.INDEX_PATH} with {n} paper(s).")
        return 0
    finally:
        conn.close()


if __name__ == "__main__":
    raise SystemExit(main())
