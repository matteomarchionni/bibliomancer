"""
compare_models.py - A/B benchmark local models on the same papers.

Runs the *production* prompt, schema and extraction path (imported from
kb_core) against several LM Studio models in turn, so the comparison reflects
what the weekly run would actually produce.

Nothing here touches manifest.db, Notes/ or synthesis_matrix.md. All output
goes to cache/compare/<run>/ so a benchmark can never contaminate the real
knowledge base.

Typical use - compare the incumbent against three quantisations of a new model
on the papers already catalogued by Qwen (which act as ground truth):

    python compare_models.py ^
        --models qwen/qwen3.8-27b qwen/qwen3.5-9b@q4_k_m ^
                 qwen/qwen3.5-9b@q6_k qwen/qwen3.5-9b@q8_0 ^
        --from-db 3

Other paper selectors:
    --paper Niches --paper Klughammer      # by relative-path substring
    --from-db 5                            # first N already-catalogued papers

Each model is loaded alone (previous ones unloaded first), so VRAM figures and
timings are not contaminated by a co-resident model. A model that fails to load
- the expected outcome if a quantisation does not fit in 12 GB - is recorded as
a failed configuration and the run continues.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import statistics
import subprocess
import sys
import time
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
_SRC = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src")
sys.path.insert(0, _SRC)
from bibliomancer import core as kb

from bibliomancer._shared.lmstudio import find_lms  # noqa: E402

LMS = find_lms(os.environ.get("BIBLIOMANCER_LMS", ""))
BENCH_ID = "bench"          # identifier every model is loaded under
COMPARE_DIR = os.path.join(kb.CACHE_DIR, "compare")


# --------------------------------------------------------------------------
# lms helpers
# --------------------------------------------------------------------------
def _lms(*args, timeout=1800):
    try:
        p = subprocess.run(
            [LMS, *args], capture_output=True, text=True,
            timeout=timeout, encoding="utf-8", errors="replace",
        )
        return p.returncode, (p.stdout or "") + (p.stderr or "")
    except subprocess.TimeoutExpired:
        return 1, f"timeout after {timeout}s"
    except OSError as exc:
        return 1, f"cannot run lms: {exc}"


def unload_all(log) -> None:
    rc, out = _lms("unload", "--all", timeout=300)
    if rc != 0 and "no model" not in out.lower():
        log(f"    (unload returned {rc}: {out.strip()[:120]})")
    time.sleep(2)


def load_model(spec: str, ctx: int, log):
    """Load one model alone. Returns (ok, seconds, detail)."""
    unload_all(log)
    t0 = time.time()
    rc, out = _lms("load", spec, "--identifier", BENCH_ID,
                   "--context-length", str(ctx), "--ttl", "7200", "-y")
    dt = time.time() - t0
    if rc != 0:
        return False, dt, re.sub(r"\s+", " ", out)[:300]
    return True, dt, "loaded"


def gpu_memory_mib():
    try:
        p = subprocess.run(
            ["nvidia-smi", "--query-gpu=memory.used", "--format=csv,noheader,nounits"],
            capture_output=True, text=True, timeout=30,
        )
        return int(p.stdout.strip().splitlines()[0])
    except Exception:
        return None


# --------------------------------------------------------------------------
# Inference - mirrors kb_core.query_lm but returns timing and token usage
# --------------------------------------------------------------------------
def run_paper(text: str, max_tokens: int):
    import requests

    payload = {
        "model": BENCH_ID,
        "messages": [
            {"role": "system", "content": kb.SYSTEM_PROMPT},
            {"role": "user", "content": f"Paper text:\n{text[:kb.MAX_CHARS]}"},
        ],
        "temperature": 0.1,
        "max_tokens": max_tokens,
        "stream": False,
        "chat_template_kwargs": {"enable_thinking": False},
        "response_format": {
            "type": "json_schema",
            "json_schema": {"name": "paper_record", "strict": True,
                            "schema": kb.PAPER_SCHEMA},
        },
    }

    t0 = time.time()
    try:
        resp = requests.post(
            f"{kb.LM_BASE}/chat/completions",
            headers={"Content-Type": "application/json"},
            json=payload, timeout=kb.REQUEST_TIMEOUT,
        )
        resp.raise_for_status()
        body = resp.json()
    except Exception as exc:
        return {"error": f"{type(exc).__name__}: {exc}", "seconds": time.time() - t0}
    seconds = time.time() - t0

    usage = body.get("usage") or {}
    out = {
        "seconds": seconds,
        "prompt_tokens": usage.get("prompt_tokens"),
        "completion_tokens": usage.get("completion_tokens"),
    }
    if out["completion_tokens"]:
        out["gen_tok_per_s"] = out["completion_tokens"] / seconds

    try:
        choice = body["choices"][0]
        content = choice["message"].get("content") or ""
        if choice.get("finish_reason") == "length":
            out["error"] = "truncated (max_tokens reached)"
            return out
        out["record"] = kb.normalise_record(json.loads(kb._strip_to_json(content)))
    except Exception as exc:
        out["error"] = f"{type(exc).__name__}: {exc}"
    return out


# --------------------------------------------------------------------------
# Paper selection
# --------------------------------------------------------------------------
def select_papers(args):
    """Returns [(full_path, rel_path, file_hash)]."""
    conn = kb.connect()
    try:
        chosen = []
        if args.from_db:
            rows = conn.execute(
                "SELECT file_hash, rel_path FROM processed_papers "
                "ORDER BY processed_at LIMIT ?", (args.from_db,),
            ).fetchall()
            for r in rows:
                full = os.path.join(kb.PAPERS_DIR, r["rel_path"])
                if os.path.exists(full):
                    chosen.append((full, r["rel_path"], r["file_hash"]))
        if args.paper:
            needles = [s.lower() for s in args.paper]
            have = {c[2] for c in chosen}
            for full, rel in kb.iter_pdfs():
                if any(n in rel.lower() for n in needles):
                    digest = kb.hash_with_cache(conn, full, rel)
                    if digest not in have:
                        have.add(digest)
                        chosen.append((full, rel, digest))
        return chosen
    finally:
        conn.close()


def slug(spec: str) -> str:
    return re.sub(r"[^A-Za-z0-9]+", "_", spec).strip("_")


# --------------------------------------------------------------------------
def write_report(out_dir, run_name, args, papers, results) -> str:
    def mean_of(vals, fmt="{:.1f}"):
        return fmt.format(statistics.mean(vals)) if vals else "-"

    L = [
        f"# Model comparison - {run_name}",
        "",
        f"_{len(papers)} paper(s), context {args.context_length}, "
        f"max_tokens {args.max_tokens}, generated {kb.now_iso()}_",
        "",
        "Same system prompt, same JSON schema and the same extracted text as the "
        "production weekly run. Each model was loaded alone, so VRAM and timing "
        "figures are not contaminated by a co-resident model.",
        "",
        "## Configurations",
        "",
        "| Model | Loaded | Load time | VRAM | Note |",
        "|---|---|---|---|---|",
    ]
    for spec, e in results.items():
        c = e["config"]
        vram = f"{c['vram_mib']} MiB" if c["vram_mib"] else "-"
        L.append(f"| `{spec}` | {'yes' if c['loaded'] else '**NO**'} "
                 f"| {c['load_seconds']}s | {vram} | {c['detail'][:70]} |")

    L += ["", "## Throughput", "",
          "| Model | Papers OK | Mean s/paper | Median | Mean out tok | Mean tok/s |",
          "|---|---|---|---|---|---|"]
    for spec, e in results.items():
        got = [p for p in e["papers"].values() if "record" in p]
        if not got:
            L.append(f"| `{spec}` | 0/{len(papers)} | - | - | - | - |")
            continue
        secs = [p["seconds"] for p in got]
        tok = [p["completion_tokens"] for p in got if p.get("completion_tokens")]
        tps = [p["gen_tok_per_s"] for p in got if p.get("gen_tok_per_s")]
        L.append(f"| `{spec}` | {len(got)}/{len(papers)} | {mean_of(secs)} "
                 f"| {statistics.median(secs):.1f} | {mean_of(tok, '{:.0f}')} "
                 f"| {mean_of(tps)} |")

    L += ["", "## Side-by-side output", ""]
    for _, rel, digest in papers:
        L += [f"### {rel}", "",
              "| Model | Year | Authors | Fields | Keywords | Findings | Limitations |",
              "|---|---|---|---|---|---|---|"]
        for spec, e in results.items():
            p = e["papers"].get(digest)
            if not p or "record" not in p:
                L.append(f"| `{spec}` | - | - | - | - | - | - |")
                continue
            r = p["record"]
            L.append(f"| `{spec}` | {r['year']} | {r['authors'][:44]} "
                     f"| {len(r['research_fields'])} | {len(r['keywords'])} "
                     f"| {len(r['key_findings'])} | {len(r['limitations'])} |")
        L.append("")
        for spec, e in results.items():
            p = e["papers"].get(digest)
            if not p or "record" not in p:
                L += [f"**`{spec}`** - no output ({p.get('error') if p else 'not run'})", ""]
                continue
            r = p["record"]
            L += [f"**`{spec}`**", "",
                  f"- *Title:* {r['title']}",
                  f"- *Takeaway:* {r['core_takeaway']}",
                  "- *Limitations:*"]
            L += [f"    - {x}" for x in r["limitations"]] or ["    - _none_"]
            L.append("")

    path = os.path.join(out_dir, "report.md")
    with open(path, "w", encoding="utf-8") as fh:
        fh.write("\n".join(L) + "\n")
    return path


# --------------------------------------------------------------------------
def main() -> int:
    ap = argparse.ArgumentParser(description="Benchmark local models on the same papers.")
    ap.add_argument("--models", nargs="+", required=True,
                    help="LM Studio model specs, e.g. qwen/qwen3.5-9b@q8_0")
    ap.add_argument("--paper", action="append", default=[],
                    help="relative-path substring selecting a paper (repeatable)")
    ap.add_argument("--from-db", type=int, default=0,
                    help="use the first N already-catalogued papers as the test set")
    ap.add_argument("--context-length", type=int, default=16384)
    ap.add_argument("--max-tokens", type=int, default=kb.MAX_TOKENS)
    ap.add_argument("--run", default=None, help="run folder name (default: timestamp)")
    ap.add_argument("--system-prompt-file", default=None,
                    help="UTF-8 file whose contents replace kb_core.SYSTEM_PROMPT "
                         "for this run (prompt A/B testing; production untouched)")
    ap.add_argument("--keep-loaded", action="store_true",
                    help="leave the last model loaded instead of unloading at the end")
    args = ap.parse_args()

    log = kb.Logger("compare")
    kb.init_db()

    if args.system_prompt_file:
        with open(args.system_prompt_file, encoding="utf-8") as fh:
            kb.SYSTEM_PROMPT = fh.read().strip()
        log(f"Using alternate system prompt from {args.system_prompt_file} "
            f"({len(kb.SYSTEM_PROMPT)} chars)")

    papers = select_papers(args)
    if not papers:
        log("No papers selected - use --from-db N or --paper SUBSTR.")
        return 1

    run_name = args.run or datetime.now().strftime("cmp_%Y%m%d_%H%M%S")
    out_dir = os.path.join(COMPARE_DIR, run_name)
    os.makedirs(out_dir, exist_ok=True)

    log("=" * 70)
    log(f"Comparison run '{run_name}'")
    log(f"{len(papers)} paper(s) x {len(args.models)} model(s), ctx={args.context_length}")

    # Extract text once up front so PDF parsing is never timed as model cost.
    texts = {}
    for full, rel, digest in papers:
        text, info = kb.extract_pdf_text(full, digest)
        if info:
            log(f"    SKIP {rel}: {info}")
            continue
        texts[digest] = text
        log(f"    paper: {rel} ({len(text)} chars)")
    papers = [p for p in papers if p[2] in texts]
    if not papers:
        log("No usable paper text.")
        return 1

    results = {}
    for spec in args.models:
        log("-" * 70)
        log(f"MODEL {spec}")
        mdir = os.path.join(out_dir, slug(spec))
        os.makedirs(mdir, exist_ok=True)

        ok, load_s, detail = load_model(spec, args.context_length, log)
        entry = {"config": {"spec": spec, "loaded": ok,
                            "load_seconds": round(load_s, 1), "detail": detail,
                            "vram_mib": gpu_memory_mib() if ok else None},
                 "papers": {}}
        results[spec] = entry

        if not ok:
            log(f"    LOAD FAILED after {load_s:.1f}s: {detail}")
            log("    (expected if this quantisation does not fit in VRAM)")
            continue
        log(f"    loaded in {load_s:.1f}s, VRAM {entry['config']['vram_mib']} MiB")

        for i, (full, rel, digest) in enumerate(papers, 1):
            r = run_paper(texts[digest], args.max_tokens)
            entry["papers"][digest] = {"rel_path": rel, **r}
            if "record" in r:
                note = kb.render_note(r["record"], digest, rel, source=spec)
                with open(os.path.join(mdir, f"{digest[:8]}.md"), "w", encoding="utf-8") as fh:
                    fh.write(note)
                tps = r.get("gen_tok_per_s")
                log(f"    [{i}/{len(papers)}] {r['seconds']:6.1f}s  "
                    f"{r.get('completion_tokens') or '?'} out tok"
                    f"{f'  {tps:.1f} tok/s' if tps else ''}  {rel[:44]}")
            else:
                log(f"    [{i}/{len(papers)}] FAILED after {r['seconds']:.1f}s: {r.get('error')}")

        with open(os.path.join(mdir, "raw.json"), "w", encoding="utf-8") as fh:
            json.dump(entry, fh, ensure_ascii=False, indent=2)

    if not args.keep_loaded:
        unload_all(log)

    log("-" * 70)
    log(f"Report: {write_report(out_dir, run_name, args, papers, results)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
