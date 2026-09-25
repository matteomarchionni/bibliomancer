"""
kb_dedup.py - Remove duplicate papers that share a DOI.

The same paper downloaded twice has different bytes (so SHA-256 dedup cannot
see it) but the same DOI. Policy, as decided 2026-09-23:

  * keep the LARGER file (more bytes), remove the other one
  * removal = the PDF, its catalogue row, its note, its scan-cache entry
  * report everything in the log, and append every deletion to
    cache/logs/dedup_history.tsv so a file can be recovered from the Google
    Drive trash (kept 30 days) if ever needed

One safety guard on top of that policy: the two catalogue records must also
have matching titles. The DOI is read from the first two pages, and a
commentary or news piece can print the DOI of the article it discusses - so a
shared DOI with different titles is reported for manual review, never deleted.

    python kb_dedup.py            # dry run: report only
    python kb_dedup.py --apply    # act (the pipeline runs this after each update)
"""

from __future__ import annotations

import argparse
import difflib
import os
import re

from . import core as kb

HISTORY = os.path.join(kb.LOG_DIR, "dedup_history.tsv")
TITLE_SIMILARITY = 0.85
# Word-set Jaccard overlap of the first ~3000 characters. Calibrated
# 2026-09-23 on all 32 shared-DOI groups: genuine duplicates scored 0.87-1.00;
# the four false pairs (a poster vs its article, a commentary vs the paper it
# discusses, a template placeholder DOI, a supplement vs its paper) scored
# 0.04-0.16. One genuine duplicate - an NIH author manuscript vs the typeset
# journal version - scored 0.21 and is sent to review rather than deleted:
# a missed duplicate costs one extra note, a wrong deletion costs a paper.
CONTENT_OVERLAP = 0.40


def first_page_words(file_hash: str) -> set[str] | None:
    try:
        with open(kb.text_cache_path(file_hash), encoding="utf-8") as fh:
            t = fh.read(3000)
    except OSError:
        return None
    return set(re.findall(r"[a-z]{4,}", t.lower()))


def content_overlap(h1: str, h2: str) -> float | None:
    a, b = first_page_words(h1), first_page_words(h2)
    if not a or not b:
        return None                               # cannot verify -> review
    return len(a & b) / len(a | b)


def normalise(conn, log, apply: bool) -> int:
    """
    Bring stored rows in line with the current classification rules before
    grouping: a file now recognised as supplementary, or carrying a DOI the
    current rules reject (template placeholders), loses that DOI and has its
    note re-rendered from the catalogue - no model call involved.
    """
    fixed = 0
    for r in conn.execute("SELECT file_hash, rel_path, doi, doc_type FROM processed_papers").fetchall():
        dtype = kb.classify_doc(r["rel_path"])
        bad_doi = bool(r["doi"]) and (dtype == "supplementary" or not kb.doi_is_plausible(r["doi"]))
        if dtype == (r["doc_type"] or "paper") and not bad_doi:
            continue
        fixed += 1
        new_doi = r["doi"]
        if bad_doi:
            new_doi = None
            if dtype != "supplementary":          # try to recover the real one
                try:
                    with open(kb.text_cache_path(r["file_hash"]), encoding="utf-8") as fh:
                        new_doi = kb.pdf_facts(os.path.join(kb.PAPERS_DIR, r["rel_path"]),
                                               fh.read())["doi"]
                except OSError:
                    pass
        log(f"  FIX    {r['rel_path'][:64]}  doc_type={dtype}"
            + (f", DOI {r['doi']} -> {new_doi or 'none'}" if bad_doi else ""))
        if apply:
            conn.execute("UPDATE processed_papers SET doc_type = ?, doi = ? WHERE file_hash = ?",
                         (dtype, new_doi, r["file_hash"]))
            conn.commit()
            kb.rerender_note(conn, r["file_hash"])
    return fixed


def write_history(doi, removed_path, removed_bytes, kept_path, kept_bytes) -> None:
    os.makedirs(kb.LOG_DIR, exist_ok=True)
    new = not os.path.exists(HISTORY)
    with open(HISTORY, "a", encoding="utf-8") as fh:
        if new:
            fh.write("removed_at\tdoi\tremoved_path\tremoved_bytes\tkept_path\tkept_bytes\n")
        fh.write(f"{kb.now_iso()}\t{doi}\t{removed_path}\t{removed_bytes}\t"
                 f"{kept_path}\t{kept_bytes}\n")


def remove_identical_copies(conn, file_hash, doi, keep, log) -> int:
    """
    Byte-identical copies of a removed file share its hash, so they were never
    catalogued separately - the catalogued copy stood in for them. Once that
    copy is removed they resurface as 'new' and would be re-summarised and
    deleted again on every run (seen 2026-09-23 with a third Wu-Swarbrick copy).
    They are the same bytes as a file the policy already removed, so they go too.
    """
    n = 0
    for r in conn.execute("SELECT rel_path, size FROM scan_cache WHERE file_hash = ?",
                          (file_hash,)).fetchall():
        full = os.path.join(kb.PAPERS_DIR, r["rel_path"])
        if os.path.exists(full):
            try:
                os.remove(full)
            except OSError as exc:
                log(f"           could not delete identical copy {r['rel_path']} ({exc})")
                continue
            log(f"    REMOVE {r['size']:>10} B  {r['rel_path']}  (byte-identical copy)")
            write_history(doi, r["rel_path"], r["size"], keep["rel_path"], keep["size"])
            n += 1
        conn.execute("DELETE FROM scan_cache WHERE rel_path = ?", (r["rel_path"],))
    conn.commit()
    return n


def pending_duplicates(conn, log, apply: bool) -> int:
    """
    Pre-ingest check: an incoming PDF whose DOI and first page match a paper
    already catalogued is a duplicate, found BEFORE any model time is spent on
    it. Same policy as below - the larger file wins:
      * incoming is not larger -> delete the incoming PDF now
      * incoming is larger     -> leave it; it is ingested, and the post-run
                                  pass then removes the smaller catalogued copy
    """
    cat = {r["doi"]: r for r in conn.execute(
        "SELECT file_hash, rel_path, doi FROM processed_papers WHERE doi IS NOT NULL")}
    removed = 0
    for full, rel, digest in kb.pending_papers(conn, include_failed=True):
        text, info = kb.extract_pdf_text(full, digest)
        if info:
            continue                                  # scanned etc.: no DOI to compare
        doi = kb.pdf_facts(full, text)["doi"]
        other = cat.get(doi) if doi else None
        if other is None:
            continue
        ov = content_overlap(digest, other["file_hash"])
        if ov is None or ov < CONTENT_OVERLAP:
            log(f"  REVIEW incoming {rel}: DOI {doi} already catalogued but first-page "
                f"overlap {'n/a' if ov is None else f'{ov:.2f}'} - will be ingested")
            continue
        size_new = os.path.getsize(full)
        other_full = os.path.join(kb.PAPERS_DIR, other["rel_path"])
        size_old = os.path.getsize(other_full) if os.path.exists(other_full) else -1
        if size_new > size_old:
            log(f"  KEEP incoming {rel} (larger than catalogued {other['rel_path']}); "
                f"the smaller copy is removed after ingestion")
            continue
        log(f"  DUPLICATE incoming {rel} ({size_new} B) of {other['rel_path']} "
            f"({size_old} B), overlap {ov:.2f} -> {'removed' if apply else 'would remove'}")
        if apply:
            try:
                os.remove(full)
            except OSError as exc:
                log(f"           could not delete ({exc})")
                continue
            conn.execute("DELETE FROM scan_cache WHERE rel_path = ?", (rel,))
            conn.execute("DELETE FROM failures WHERE file_hash = ?", (digest,))
            conn.commit()
            write_history(doi, rel, size_new, other["rel_path"], size_old)
            removed += 1
    return removed


def _norm(t: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", (t or "").lower()).strip()


def same_title(a: str, b: str) -> bool:
    na, nb = _norm(a), _norm(b)
    if not na or not nb:
        return False
    if na == nb or na in nb or nb in na:
        return True
    return difflib.SequenceMatcher(None, na, nb).ratio() >= TITLE_SIMILARITY


def main() -> int:
    ap = argparse.ArgumentParser(description="Remove duplicate papers sharing a DOI.")
    ap.add_argument("--apply", action="store_true", help="act; without it, report only")
    ap.add_argument("--pending", action="store_true",
                    help="pre-ingest pass only: drop incoming PDFs that duplicate catalogued papers")
    args = ap.parse_args()

    log = kb.Logger("dedup")
    kb.init_db()
    conn = kb.connect()
    removed = review = 0
    if args.pending:
        try:
            log("=" * 64)
            log(f"Pre-ingest duplicate check {'APPLY' if args.apply else 'DRY RUN'}")
            n = pending_duplicates(conn, log, args.apply)
            log(f"Pre-ingest check done: {n} incoming duplicate(s) removed")
            return 0
        finally:
            conn.close()
    try:
        log("=" * 64)
        log(f"Dedup {'APPLY' if args.apply else 'DRY RUN'}")
        fixed = normalise(conn, log, args.apply)
        groups = conn.execute(
            "SELECT doi FROM processed_papers WHERE doi IS NOT NULL AND doi != '' "
            "GROUP BY doi HAVING COUNT(*) > 1").fetchall()
        if not args.apply:           # dry run: simulate the normalisation's effect
            groups = [g for g in groups if kb.doi_is_plausible(g["doi"])]
        log(f"{fixed} row(s) normalised; {len(groups)} DOI(s) shared by more than one paper")

        for g in groups:
            doi = g["doi"]
            rows = conn.execute(
                "SELECT file_hash, rel_path, title, note_filename FROM processed_papers "
                "WHERE doi = ?", (doi,)).fetchall()
            members = []
            for r in rows:
                full = os.path.join(kb.PAPERS_DIR, r["rel_path"])
                size = os.path.getsize(full) if os.path.exists(full) else -1
                members.append({**dict(r), "full": full, "size": size})

            # Guard: every member must carry the same title as the first.
            if not all(same_title(members[0]["title"], m["title"]) for m in members[1:]):
                review += 1
                log(f"  REVIEW {doi}: same DOI, different titles - not touched")
                for m in members:
                    log(f"         {m['size']:>10} B  {m['rel_path']}  |  {m['title'][:60]}")
                continue

            if not args.apply:       # dry run: drop members normalise() would demote
                members = [m for m in members if kb.classify_doc(m["rel_path"]) == "paper"]
                if len(members) < 2:
                    continue
            members.sort(key=lambda m: (m["size"], m["rel_path"]), reverse=True)
            keep, drop = members[0], members[1:]
            log(f"  {doi}")
            log(f"    KEEP   {keep['size']:>10} B  {keep['rel_path']}")
            for d in drop:
                ov = content_overlap(keep["file_hash"], d["file_hash"])
                if ov is None or ov < CONTENT_OVERLAP:
                    review += 1
                    log(f"    REVIEW {d['size']:>10} B  {d['rel_path']}  "
                        f"(first-page overlap {'n/a' if ov is None else f'{ov:.2f}'} "
                        f"< {CONTENT_OVERLAP} - different document? not touched)")
                    continue
                log(f"    REMOVE {d['size']:>10} B  {d['rel_path']}  (overlap {ov:.2f})")
                if not args.apply:
                    continue
                if d["size"] >= 0:
                    try:
                        os.remove(d["full"])
                    except OSError as exc:
                        log(f"           could not delete PDF ({exc}); catalogue entry kept")
                        continue
                note = os.path.join(kb.NOTES_DIR, d["note_filename"] or "")
                if d["note_filename"] and os.path.exists(note):
                    os.remove(note)
                conn.execute("DELETE FROM processed_papers WHERE file_hash = ?", (d["file_hash"],))
                conn.execute("DELETE FROM scan_cache WHERE rel_path = ?", (d["rel_path"],))
                conn.execute("DELETE FROM failures WHERE file_hash = ?", (d["file_hash"],))
                conn.commit()
                removed += 1
                write_history(doi, d["rel_path"], d["size"], keep["rel_path"], keep["size"])
                removed += remove_identical_copies(conn, d["file_hash"], doi, keep, log)

        if args.apply and removed:
            kb.rebuild_matrix(conn)
        log(f"Dedup done: {removed} removed, {review} left for manual review"
            + ("" if args.apply else " (dry run - nothing changed)"))
        if removed:
            log(f"Deletions recorded in {HISTORY}; removed PDFs sit in the "
                f"Google Drive trash for 30 days.")
        return 0
    finally:
        conn.close()


if __name__ == "__main__":
    raise SystemExit(main())
