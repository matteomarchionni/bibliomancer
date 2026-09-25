"""
kb_batch.py - Bulk seeding path, driven by Claude Code rather than the local model.

The initial knowledge base (hundreds of existing papers) is built by Claude, not
by Qwen. This script is the bridge: it does the PDF work locally and hands Claude
plain text, then takes Claude's structured records back and writes them through
exactly the same note/DB/matrix code the weekly Qwen run uses.

Workflow:

  1. python kb_batch.py export --limit 40
        -> cache/batches/<batch>/0001_<hash8>.txt   (extracted text, one per paper)
        -> cache/batches/<batch>/manifest.jsonl     (index -> hash, rel_path, pages)

  2. Claude reads those .txt files and writes cache/batches/<batch>/records.jsonl,
     one JSON object per line, each containing "file_hash" plus the ten record
     fields (title, authors, year, research_fields, keywords, research_question,
     methodology, key_findings, limitations, core_takeaway).

  3. python kb_batch.py import cache/batches/<batch>/records.jsonl
        -> writes Notes/*.md, fills manifest.db (source='claude'), rebuilds the matrix.

Other commands:
    python kb_batch.py status
    python kb_batch.py list-batches
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
_SRC = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src")
sys.path.insert(0, _SRC)
from bibliomancer import core as kb


# --------------------------------------------------------------------------
def cmd_export(args) -> int:
    log = kb.Logger("batch")
    kb.init_db()
    conn = kb.connect()
    try:
        batch = args.batch or datetime.now().strftime("batch_%Y%m%d_%H%M%S")
        out_dir = os.path.join(kb.BATCH_DIR, batch)
        os.makedirs(out_dir, exist_ok=True)

        pending = kb.pending_papers(conn, include_failed=args.include_failed)
        log(f"{len(pending)} paper(s) not yet catalogued.")
        if args.skip_supplementary:
            before = len(pending)
            pending = [p for p in pending if kb.classify_doc(p[1]) == "paper"]
            log(f"Skipped {before - len(pending)} supplementary file(s).")
        if args.offset:
            pending = pending[args.offset:]
        if args.limit:
            pending = pending[: args.limit]

        manifest_path = os.path.join(out_dir, "manifest.jsonl")
        written = skipped = 0
        with open(manifest_path, "w", encoding="utf-8") as mf:
            for i, (full, rel, digest) in enumerate(pending, 1):
                text, info = kb.extract_pdf_text(full, digest)
                if info:
                    log(f"  SKIP {rel}: {info}")
                    kb.record_failure(conn, digest, rel, info)
                    skipped += 1
                    continue
                name = f"{i:04d}_{digest[:8]}.txt"
                with open(os.path.join(out_dir, name), "w", encoding="utf-8") as fh:
                    fh.write(text[: kb.MAX_CHARS])
                mf.write(json.dumps({
                    "text_file": name,
                    "file_hash": digest,
                    "rel_path": rel,
                    "filename": os.path.basename(full),
                    "chars": len(text),
                }, ensure_ascii=False) + "\n")
                written += 1

        log(f"Exported {written} paper(s), skipped {skipped}.")
        log(f"Batch folder: {out_dir}")
        print(out_dir)
        return 0
    finally:
        conn.close()


# --------------------------------------------------------------------------
def cmd_import(args) -> int:
    log = kb.Logger("batch")
    kb.init_db()
    conn = kb.connect()
    try:
        path = args.records
        if not os.path.exists(path):
            log(f"No such file: {path}")
            return 1

        # rel_path lookup for records that do not carry one
        by_hash = {
            r["file_hash"]: r["rel_path"]
            for r in conn.execute("SELECT file_hash, rel_path FROM scan_cache")
        }

        added = bad = 0
        with open(path, encoding="utf-8") as fh:
            for lineno, line in enumerate(fh, 1):
                line = line.strip()
                if not line or line.startswith("//"):
                    continue
                try:
                    raw = json.loads(line)
                except json.JSONDecodeError as exc:
                    log(f"  line {lineno}: bad JSON: {exc}")
                    bad += 1
                    continue

                digest = raw.get("file_hash", "")
                if len(digest) != 64:
                    log(f"  line {lineno}: missing/!=64-char file_hash")
                    bad += 1
                    continue

                rel = raw.get("rel_path") or by_hash.get(digest)
                if not rel:
                    log(f"  line {lineno}: unknown file_hash {digest[:8]} (run export first)")
                    bad += 1
                    continue

                try:
                    record = kb.normalise_record(raw)
                except ValueError as exc:
                    log(f"  line {lineno}: {exc}")
                    bad += 1
                    continue

                note = kb.commit_paper(
                    conn, record, digest, rel,
                    raw.get("filename") or os.path.basename(rel),
                    source=args.source,
                )
                added += 1
                if added % 25 == 0:
                    log(f"  ... {added} imported")

        n = kb.rebuild_matrix(conn)
        log(f"Imported {added} record(s), {bad} rejected. Matrix now lists {n} paper(s).")
        return 0 if bad == 0 else 2
    finally:
        conn.close()


# --------------------------------------------------------------------------
def cmd_status(args) -> int:
    kb.init_db()
    conn = kb.connect()
    try:
        total = conn.execute("SELECT COUNT(*) FROM processed_papers").fetchone()[0]
        by_src = conn.execute(
            "SELECT source, COUNT(*) c FROM processed_papers GROUP BY source").fetchall()
        failures = conn.execute(
            "SELECT reason, COUNT(*) c FROM failures GROUP BY reason ORDER BY c DESC LIMIT 10"
        ).fetchall()
        pending = kb.pending_papers(conn)

        print(f"PDFs on disk        : {len(kb.iter_pdfs())}")
        print(f"Catalogued          : {total}")
        for r in by_src:
            print(f"   by {r['source'] or '?':<8}       : {r['c']}")
        print(f"Pending (new)       : {len(pending)}")
        print(f"Notes on disk       : {len([f for f in os.listdir(kb.NOTES_DIR) if f.endswith('.md')])}")
        if failures:
            print("Failures:")
            for r in failures:
                print(f"   {r['c']:>4}  {r['reason'][:90]}")
        return 0
    finally:
        conn.close()


def cmd_list_batches(args) -> int:
    if not os.path.isdir(kb.BATCH_DIR):
        return 0
    for name in sorted(os.listdir(kb.BATCH_DIR)):
        d = os.path.join(kb.BATCH_DIR, name)
        if not os.path.isdir(d):
            continue
        txts = len([f for f in os.listdir(d) if f.endswith(".txt")])
        has_rec = os.path.exists(os.path.join(d, "records.jsonl"))
        print(f"{name:<32} {txts:>4} texts   records.jsonl: {'yes' if has_rec else 'no'}")
    return 0


# --------------------------------------------------------------------------
def main() -> int:
    ap = argparse.ArgumentParser(description="Claude-driven bulk seeding for the knowledge base.")
    sub = ap.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("export", help="extract text for papers not yet catalogued")
    p.add_argument("--limit", type=int, default=0)
    p.add_argument("--offset", type=int, default=0)
    p.add_argument("--batch", default=None, help="batch folder name (default: timestamp)")
    p.add_argument("--include-failed", action="store_true")
    p.add_argument("--skip-supplementary", action="store_true",
                   help="exclude supplementary material / SI files")
    p.set_defaults(func=cmd_export)

    p = sub.add_parser("import", help="ingest a records.jsonl produced by Claude")
    p.add_argument("records")
    p.add_argument("--source", default="claude")
    p.set_defaults(func=cmd_import)

    sub.add_parser("status").set_defaults(func=cmd_status)
    sub.add_parser("list-batches").set_defaults(func=cmd_list_batches)

    args = ap.parse_args()
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
