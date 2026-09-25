"""check - is everything in place for a run? Changes nothing.

    python -m bibliomancer check            folders, LM Studio, model, embeddings
    python -m bibliomancer check --paper    ...and summarise one random catalogued
                                        paper with the model (nothing is saved)

Same idea as PhotoMancer's `check`. Exit code 0 = ready, 1 = something
needs attention (every problem is printed with what to do).
"""

from __future__ import annotations

import argparse
import os
import random
import shutil
import time

from . import core as kb
from ._shared import lmstudio, paths
from ._shared.embed import Embedder


def main() -> int:
    ap = argparse.ArgumentParser(prog="bibliomancer check", description=__doc__)
    ap.add_argument("--paper", action="store_true", help="also summarise one catalogued paper")
    args = ap.parse_args()
    problems = 0

    def ok(msg):
        print(f"  OK    {msg}", flush=True)

    def bad(msg, fix):
        nonlocal problems
        problems += 1
        print(f"  FAIL  {msg}\n        -> {fix}", flush=True)

    print("=== check settings and folders ===", flush=True)
    ok(f"config: {kb.CONFIG_PATH}" + ("" if os.path.exists(kb.CONFIG_PATH) else " (not created yet)"))
    if kb.PAPERS_DIR and os.path.isdir(kb.PAPERS_DIR):
        ok(f"PDF library: {kb.PAPERS_DIR} ({len(kb.iter_pdfs())} PDFs)")
    else:
        bad(f"PDF library not found: {kb.PAPERS_DIR!r}", "set papers_dir (Options > Model and folders)")
    try:
        os.makedirs(kb.KB_HOME, exist_ok=True)
        free = shutil.disk_usage(kb.KB_HOME).free / 1e9
        ok(f"data folder: {kb.KB_HOME} ({free:.0f} GB free)")
        if paths.looks_like_cloud_folder(kb.KB_HOME):
            bad("the data folder is on a cloud drive", "move data_dir to a local disk "
                "(SQLite there can be locked by the sync client mid-run)")
    except OSError as exc:
        bad(f"data folder not writable: {exc}", "choose another data_dir")
    if kb.EXPORT_DIR:
        (ok if os.path.isdir(os.path.dirname(kb.EXPORT_DIR.rstrip("\\/")) or kb.EXPORT_DIR)
         else lambda m: bad(m, "check export_dir"))(f"export folder: {kb.EXPORT_DIR}")
    else:
        ok("export folder: none (no export / backup after runs)")

    print("=== check LM Studio ===", flush=True)
    lms = lmstudio.find_lms(kb.LMS_PATH)
    if lms:
        ok(f"lms: {lms}")
        good, msg = lmstudio.check_model(lms, kb.MODEL_NAME, kb.CONTEXT_LENGTH)
        (ok if good else lambda m: bad(m, "download it in LM Studio or pick another model"))(msg)
        emb = lmstudio.installed_models(lms, "embedding") or []
        if any(m.get("modelKey") == kb.EMBED_MODEL for m in emb):
            ok(f"embedding model installed: {kb.EMBED_MODEL}")
        else:
            bad(f"embedding model {kb.EMBED_MODEL} not installed",
                "LM Studio > Discover > nomic-embed-text (search by meaning needs it)")
        lmstudio.start_server(lms, out=lambda m: None)
    else:
        bad("LM Studio command line (lms) not found",
            "install LM Studio from https://lmstudio.ai and open it once")
    up, _ids = lmstudio.server_up(kb.LM_BASE, timeout=10)
    (ok if up else lambda m: bad(m, "open LM Studio, or run `lms server start`"))(
        f"server at {kb.LM_BASE}" + (" is up" if up else " is not reachable"))
    if up:
        try:
            t = time.time()
            Embedder(kb.LM_BASE, kb.EMBED_MODEL, timeout=120)(["test"], "search_query: ")
            ok(f"embeddings answer ({time.time() - t:.1f} s)")
        except Exception as exc:
            bad(f"embeddings failed: {type(exc).__name__}: {exc}", "check the embedding model")

    if args.paper and up and problems == 0:
        print("=== summarise one paper (nothing is saved) ===", flush=True)
        conn = kb.connect()
        rows = conn.execute("SELECT file_hash, title FROM processed_papers").fetchall()
        conn.close()
        cand = [r for r in rows if os.path.exists(kb.text_cache_path(r["file_hash"]))]
        if cand:
            r = random.choice(cand)
            text = open(kb.text_cache_path(r["file_hash"]), encoding="utf-8", errors="replace").read()
            t = time.time()
            rec, detail = kb.query_lm(text)
            if rec:
                ok(f"{time.time() - t:.1f} s: {rec['title'][:80]} ({rec['year']})")
                print(f"        takeaway: {rec['core_takeaway'][:200]}", flush=True)
            else:
                bad(f"the model did not return a valid summary: {detail}",
                    "try another model, or reasoning_effort '' in config.yaml")

    print(f"\n=== check finished: {'ready' if not problems else f'{problems} problem(s)'} ===",
          flush=True)
    return 1 if problems else 0


if __name__ == "__main__":
    raise SystemExit(main())
