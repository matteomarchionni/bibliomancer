"""
simulate_ingest.py - Dry-run the ingestion pipeline over a folder of PDFs.

Answers "what would happen if these were dropped into Database/?" without
touching anything: no manifest.db writes, no Notes/, no synthesis matrix, no
text cache, and no model calls. Reads each PDF directly and reports the
deterministic half of the pipeline plus the scout/project intersection.

The LLM half (title, findings, limitations) is not simulated - it needs the
GPU, which may be busy with a real ingest. The parts this does exercise are
exactly the ones that are new or deterministic: DOI and year extraction,
supplementary classification, and whether the paper matches a literature-scout
brief.

    python simulate_ingest.py --dir "D:\\Papers\\new_papers"
    python simulate_ingest.py --dir <path> --show-section   # full rendered section
"""

from __future__ import annotations

import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
_SRC = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src")
sys.path.insert(0, _SRC)
from bibliomancer import core as kb
from bibliomancer import projects as kb_projects


def read_pdf_text(path: str, max_pages: int = 4) -> str:
    """Page-marked text, matching kb_core's format, without writing the cache."""
    from bibliomancer.pdf import Encrypted, open_pdf
    try:
        doc = open_pdf(path)
    except Encrypted:
        return "<<encrypted>>"
    except Exception as exc:
        return f"<<unreadable: {exc}>>"
    try:
        parts = []
        for i in range(min(doc.page_count, max_pages)):
            try:
                parts.append(f"\n--- Page {i + 1} ---\n" + doc.text(i))
            except Exception:
                pass
        return "".join(parts)
    finally:
        doc.close()


def main() -> int:
    ap = argparse.ArgumentParser(description="Dry-run ingestion over a folder of PDFs.")
    ap.add_argument("--dir", required=True)
    ap.add_argument("--show-section", action="store_true",
                    help="print the full rendered intersection section, not a summary")
    args = ap.parse_args()
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

    if not os.path.isdir(args.dir):
        print(f"No such directory: {args.dir}")
        return 1

    pdfs = sorted(f for f in os.listdir(args.dir) if f.lower().endswith(".pdf"))
    conn = kb.connect()
    known = {r["file_hash"] for r in conn.execute("SELECT file_hash FROM processed_papers")}
    conn.close()

    print(f"SIMULATION - nothing is written. {len(pdfs)} PDF(s) in {args.dir}\n")
    matched = 0
    for f in pdfs:
        full = os.path.join(args.dir, f)
        text = read_pdf_text(full)
        facts = kb.pdf_facts(full, text)
        doc_type = kb.classify_doc(f)
        digest = kb.compute_sha256(full)
        usable = len(text.strip()) >= kb.MIN_USABLE_CHARS

        print("=" * 76)
        print(f"{f}")
        print(f"  would be      : {'SKIPPED (too little text - needs OCR)' if not usable else 'ingested'}"
              f"{'  [ALREADY IN DB]' if digest in known else ''}")
        print(f"  doc_type      : {doc_type}")
        print(f"  year (PDF)    : {facts['meta_year'] or '-'}")
        print(f"  DOI           : {facts['doi'] or '-'}")
        print(f"  PDF title     : {(facts['meta_title'] or '-')[:70]}")

        item = kb_projects.find_match(facts["doi"], facts["meta_title"])
        if not item:
            print("  scout match   : none - no intersection section")
            continue
        matched += 1
        print(f"  scout match   : YES via {item['_matched_on']} "
              f"({item.get('source_brief', '?')})")
        print(f"  projects      : {', '.join(item.get('projects') or []) or '-'}")
        print(f"  your verdict  : {item.get('user_verdict') or '-'}")
        if item.get("user_comment"):
            print(f"  your comment  : {item['user_comment'][:110]}")
        if args.show_section:
            print()
            for line in kb_projects.render_section(item).splitlines():
                print("      " + line)

    print("=" * 76)
    print(f"{matched}/{len(pdfs)} would receive an 'Intersection with personal work' section.")
    print("Nothing was written: no manifest.db, no Notes/, no matrix, no cache, no model calls.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
