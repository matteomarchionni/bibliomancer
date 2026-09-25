"""
kb_reset.py - Clear the knowledge base so it can be rebuilt from scratch.

Destructive, so it refuses to run without --yes and always takes a backup
first. It removes the catalogue and the generated notes; it never touches the
source PDFs in Database/, the Scouts briefs, or the Projects folder.

The extracted-text cache is kept by default: it is just PDF text keyed by
content hash, so keeping it makes a rebuild markedly faster and changes
nothing about the output. Use --clear-text-cache to drop it too.

    python kb_reset.py                     # dry run: show what would go
    python kb_reset.py --yes               # do it, keeping the text cache
    python kb_reset.py --yes --clear-text-cache
"""

from __future__ import annotations

import argparse
import os
import shutil
import sys
from datetime import datetime

from . import core as kb

BACKUP_DIR = os.path.join(kb.CACHE_DIR, "backups")


def _count(path: str, suffix: str = "") -> int:
    if not os.path.isdir(path):
        return 0
    return len([f for f in os.listdir(path) if f.endswith(suffix)])


def main() -> int:
    ap = argparse.ArgumentParser(description="Clear the knowledge base for a full rebuild.")
    ap.add_argument("--yes", action="store_true", help="actually do it")
    ap.add_argument("--clear-text-cache", action="store_true",
                    help="also drop cache/text (forces every PDF to be re-parsed)")
    args = ap.parse_args()
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

    notes = _count(kb.NOTES_DIR, ".md")
    texts = _count(kb.TEXT_CACHE, ".txt")
    rows = 0
    if os.path.exists(kb.DB_PATH):
        conn = kb.connect()
        try:
            rows = conn.execute("SELECT COUNT(*) FROM processed_papers").fetchone()[0]
        except Exception:
            pass
        conn.close()

    print("Would remove:")
    print(f"  catalogue rows : {rows}")
    print(f"  notes (.md)    : {notes}   in {kb.NOTES_DIR}")
    print(f"  synthesis matrix: {'yes' if os.path.exists(kb.INDEX_PATH) else 'no'}")
    print(f"  text cache     : {'yes, ' + str(texts) + ' files' if args.clear_text_cache else 'KEPT (' + str(texts) + ' files)'}")
    print("Never touched: Database/ (source PDFs), Scouts/, Projects/, scripts/")

    if not args.yes:
        print("\nDry run. Re-run with --yes to proceed.")
        return 0

    os.makedirs(BACKUP_DIR, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")

    if os.path.exists(kb.DB_PATH):
        dest = os.path.join(BACKUP_DIR, f"manifest_{stamp}.db")
        shutil.copy2(kb.DB_PATH, dest)
        print(f"\nbacked up manifest.db -> {dest}")
    if os.path.isdir(kb.NOTES_DIR) and notes:
        dest = os.path.join(BACKUP_DIR, f"Notes_{stamp}")
        shutil.copytree(kb.NOTES_DIR, dest)
        print(f"backed up Notes/      -> {dest}")

    if os.path.exists(kb.DB_PATH):
        os.remove(kb.DB_PATH)
    for f in os.listdir(kb.NOTES_DIR) if os.path.isdir(kb.NOTES_DIR) else []:
        if f.endswith(".md"):
            os.remove(os.path.join(kb.NOTES_DIR, f))
    if os.path.exists(kb.INDEX_PATH):
        os.remove(kb.INDEX_PATH)
    if args.clear_text_cache and os.path.isdir(kb.TEXT_CACHE):
        shutil.rmtree(kb.TEXT_CACHE)

    kb.init_db()
    print("\nCleared. Run an update to rebuild.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
