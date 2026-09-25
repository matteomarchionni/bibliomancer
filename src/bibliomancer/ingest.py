"""
ingest.py - Incremental ingestion using the local model (run by `update`).

Scans Database/ for PDFs that are not yet in the manifest, summarises each one
through LM Studio, writes a note, and regenerates the synthesis matrix.

Designed to be safe to run unattended:
  * exits cleanly (code 0) if LM Studio is not running - nothing is corrupted
  * a malformed PDF is recorded as a failure, it does not abort the run
  * scanned/image-only PDFs are detected and skipped rather than hallucinated
  * everything is logged to cache/logs/

Usage:
    python -m bibliomancer ingest                 # what `update` runs after setting up the model
    python -m bibliomancer ingest --limit 3       # process at most 3 papers
    python -m bibliomancer plan                   # list what would be processed (--dry-run)
    python -m bibliomancer ingest --retry-failed  # include files that previously failed
    python -m bibliomancer ingest --status        # counts only, no work

With parallel: N in config.yaml, N papers are read at the same time (the model
is then loaded with N x the context; 2 was 1.7x faster in a test on 6 papers).
"""

from __future__ import annotations

import argparse
import os
import time

from . import core as kb


def main() -> int:
    ap = argparse.ArgumentParser(description="Incremental knowledge-base update via LM Studio.")
    ap.add_argument("--limit", type=int, default=0, help="max papers to process (0 = all)")
    ap.add_argument("--dry-run", action="store_true", help="list pending papers and exit")
    ap.add_argument("--retry-failed", action="store_true", help="re-attempt files that hit the failure cap")
    ap.add_argument("--status", action="store_true", help="print counts and exit")
    ap.add_argument("--only", action="append", default=[], metavar="SUBSTR",
                    help="only papers whose relative path contains SUBSTR (repeatable)")
    ap.add_argument("--skip-supplementary", action="store_true",
                    help="ignore files that look like supplementary material / SI")
    ap.add_argument("--system-prompt-file", default=None,
                    help="UTF-8 file whose contents replace the built-in system prompt")
    ap.add_argument("--no-vision", action="store_true",
                    help="do not fall back to vision OCR for scanned PDFs")
    ap.add_argument("--max-minutes", type=float, default=0,
                    help="stop starting new papers after this many minutes (0 = no budget)")
    args = ap.parse_args()

    log = kb.Logger("weekly")
    if args.system_prompt_file:
        with open(args.system_prompt_file, encoding="utf-8") as fh:
            kb.SYSTEM_PROMPT = fh.read().strip()
        log(f"Using custom system prompt from {args.system_prompt_file} "
            f"({len(kb.SYSTEM_PROMPT)} chars)")
    kb.init_db()
    conn = kb.connect()

    try:
        if args.status:
            total = conn.execute("SELECT COUNT(*) FROM processed_papers").fetchone()[0]
            by_src = dict(conn.execute(
                "SELECT source, COUNT(*) FROM processed_papers GROUP BY source").fetchall())
            failed = conn.execute("SELECT COUNT(*) FROM failures").fetchone()[0]
            log(f"catalogued: {total} ({by_src})  failures: {failed}")
            return 0

        log("=" * 64)
        log(f"Weekly update starting. Database: {kb.PAPERS_DIR}")

        ok, detail = kb.lm_available()
        if not ok and not args.dry_run:
            log(f"ABORT: {detail}")
            log("Start LM Studio (server autostarts on launch) and re-run.")
            return 0
        if not args.dry_run:
            log(detail)

        t0 = time.time()
        pending = kb.pending_papers(conn, include_failed=args.retry_failed)
        log(f"Scan complete in {time.time() - t0:.1f}s - {len(pending)} new paper(s) pending.")

        if args.skip_supplementary:
            before = len(pending)
            pending = [p for p in pending if kb.classify_doc(p[1]) == "paper"]
            log(f"Skipped {before - len(pending)} supplementary file(s).")

        if args.only:
            needles = [s.lower() for s in args.only]
            pending = [p for p in pending if any(n in p[1].lower() for n in needles)]
            log(f"Filtered by --only to {len(pending)} paper(s).")

        if args.limit:
            pending = pending[: args.limit]
            log(f"Limited to {len(pending)} paper(s) this run.")

        if args.dry_run:
            for _, rel, digest in pending:
                log(f"  PENDING {digest[:8]}  {rel}")
            return 0

        if not pending:
            log("Nothing to do.")
            return 0

        done = failed = skipped = 0
        budget = args.max_minutes * 60 if args.max_minutes else 0
        par = kb.PARALLEL
        if par > 1:
            log(f"Reading {par} papers at a time (config parallel: {par}).")

        def work(full, digest, quiet):
            """Text + model answer for one paper; no database access (runs in a thread)."""
            out = {"notes": []}
            say = out["notes"].append if quiet else log
            text, info = kb.extract_pdf_text(full, digest)
            if info and "scanned or image-only" in info and not args.no_vision:
                # Image-only PDF: transcribe the first pages with the vision
                # projector of the model already loaded, then carry on as normal.
                say(f"    scanned PDF ({info.split('(')[0].strip()}) - trying vision OCR")
                text, info = kb.vision_extract(full, digest, log=say)
            out.update(text=text, info=info, record=None, detail="")
            if info:
                return out
            record, detail = kb.query_lm(text, file_hash=digest)
            if record is None and detail.startswith("response truncated"):
                # The answer ran out of output budget. Repeating the same request
                # cannot help, so retry once with half the input and an explicit
                # instruction to be brief.
                say(f"    truncated - retrying with {kb.RETRY_MAX_CHARS} chars, concise")
                record, detail = kb.query_lm(text, file_hash=digest,
                                             max_chars=kb.RETRY_MAX_CHARS, concise=True)
            out.update(record=record, detail=detail)
            return out

        def results():
            """(i, paper, result, seconds) in library order; at most `par` in flight."""
            from concurrent.futures import ThreadPoolExecutor
            if par <= 1:
                for i, p in enumerate(pending, 1):
                    if budget and (time.time() - t0) > budget:
                        yield i, p, None, 0
                        return
                    log(f"[{i}/{len(pending)}] {p[1]}")
                    t = time.time()
                    yield i, p, work(p[0], p[2], quiet=False), time.time() - t
                return
            with ThreadPoolExecutor(par) as ex:
                queue, it, last = [], iter(enumerate(pending, 1)), time.time()
                stop = False
                while True:
                    while not stop and len(queue) < par:
                        nxt = next(it, None)
                        if nxt is None:
                            break
                        if budget and (time.time() - t0) > budget:
                            stop = True
                            queue.append((nxt[0], nxt[1], None))
                            break
                        queue.append((nxt[0], nxt[1], ex.submit(work, nxt[1][0], nxt[1][2], True)))
                    if not queue:
                        return
                    i, p, fut = queue.pop(0)
                    if fut is None:
                        yield i, p, None, 0
                        return
                    res = fut.result()
                    log(f"[{i}/{len(pending)}] {p[1]}")
                    for note in res["notes"]:
                        log(note)
                    # seconds per paper as throughput, so the panel's ETA stays right
                    now = time.time()
                    yield i, p, res, now - last
                    last = now

        for i, (full, rel, digest), res, secs in results():
            if res is None:
                log(f"Time budget of {args.max_minutes:g} min reached - "
                    f"stopping with {len(pending) - i + 1} paper(s) still pending.")
                break
            if res["info"]:
                log(f"    SKIP  {res['info']}")
                kb.record_failure(conn, digest, rel, res["info"])
                skipped += 1
                continue
            record, detail, text = res["record"], res["detail"], res["text"]
            if record is None:
                log(f"    FAIL  {detail}")
                kb.record_failure(conn, digest, rel, detail)
                failed += 1
                continue

            # Deterministic metadata beats the model on the year field: the
            # PDF creationDate is present in ~96% of the library, whereas the
            # model answered 'Unknown' about half the time. The model still
            # wins when it has a real answer - it can read 'Published online'.
            facts = kb.pdf_facts(full, text)
            facts["text_source"] = kb.text_source(digest)
            if record["year"] == "Unknown" and facts["meta_year"]:
                record["year"] = facts["meta_year"]
                log(f"    year from PDF metadata: {facts['meta_year']}")

            note = kb.commit_paper(conn, record, digest, rel,
                                   os.path.basename(full), source=kb.MODEL_NAME, facts=facts)
            log(f"    OK    {secs:.1f}s  ->  Notes/{note}")
            log(f"          {record['year']} | {record['title'][:80]}")
            done += 1

        n = kb.rebuild_matrix(conn)
        log(f"Synthesis matrix rebuilt: {n} paper(s).")
        log(f"Done in {time.time() - t0:.1f}s - {done} added, {failed} failed, {skipped} skipped.")
        return 0
    finally:
        conn.close()


if __name__ == "__main__":
    raise SystemExit(main())
