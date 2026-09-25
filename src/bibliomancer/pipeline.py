"""
pipeline.py - The one entry point that runs the whole pipeline.

The panel, the scheduled run and the command line all call this, so every
path runs exactly the same sequence:

  update   1. start LM Studio and load the model at the right context length
           2. refresh the scout index from Scouts/*.pdf
              then drop incoming PDFs that duplicate a catalogued paper
           3. ingest new PDFs (vision OCR for scans, retry on truncation)
           4. remove duplicate papers (same DOI, same content)
           5. update the semantic-search index
           6. rebuild the synthesis matrix
           7. export to the synced folder - ALWAYS, even if a step failed,
              because every paper already written is committed and should be
              published

  rebuild  back up, clear the catalogue and notes, then `update`
  post     steps 4-7 only (no model needed except for embeddings)

    python -m bibliomancer update [--limit N] [--max-minutes M]
                              [--skip-supplementary] [--retry-failed]
                              [--no-vision] [--system-prompt-file F]
    python -m bibliomancer rebuild [same options] [--clear-text-cache]
    python -m bibliomancer post
"""

from __future__ import annotations

import argparse
import os
import subprocess
import time

from . import core as kb
from ._shared import lmstudio, paths

PY = paths.python_exe()
LMS = lmstudio.find_lms(kb.LMS_PATH)


def say(msg: str) -> None:
    print(f"\n=== {msg} ===", flush=True)


def run(argv: list[str], label: str, fatal: bool = False) -> int:
    say(label)
    t0 = time.time()
    rc = subprocess.call(argv, cwd=kb.APP_HOME, env=paths.child_env(kb._config.PKG_FILE))
    print(f"--- {label}: exit {rc}, {time.time() - t0:.0f}s", flush=True)
    if rc != 0 and fatal:
        raise SystemExit(rc)
    return rc


def module(name: str, *args: str) -> list[str]:
    """argv running bibliomancer.<name> in a child process (its own crash stays contained)."""
    if paths.FROZEN:
        return [*paths.self_command("bibliomancer", kb._config.PKG_FILE), "_module", name, *args]
    return [PY, "-u", "-m", f"bibliomancer.{name}", *args]


def installed_models(kind: str | None = "llm") -> list[dict] | None:
    return lmstudio.installed_models(LMS, kind)


def check_model(name: str) -> tuple[bool, str]:
    """Is `name` a chat model installed in LM Studio that can run this pipeline?"""
    return lmstudio.check_model(LMS, name, kb.CONTEXT_LENGTH)


def lm_setup() -> None:
    lmstudio.setup(LMS, kb.MODEL_NAME, kb.CONTEXT_LENGTH, parallel=kb.PARALLEL,
                   out=lambda m: print(("\n" if m.startswith("===") else "") + m, flush=True))


def post_steps() -> None:
    run(module("dedup", "--apply"), "remove duplicates")
    run(module("embed"), "update semantic index")
    run(module("matrix"), "rebuild synthesis matrix")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="bibliomancer", description="Run the knowledge-base pipeline.")
    ap.add_argument("mode", choices=["update", "rebuild", "post"])
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--max-minutes", type=float, default=0)
    ap.add_argument("--skip-supplementary", action="store_true")
    ap.add_argument("--retry-failed", action="store_true")
    ap.add_argument("--no-vision", action="store_true")
    ap.add_argument("--system-prompt-file", default=None)
    ap.add_argument("--clear-text-cache", action="store_true")
    ap.add_argument("--no-lm-setup", action="store_true",
                    help="assume the model is already loaded correctly")
    args = ap.parse_args(argv)

    t0 = time.time()
    print(f"Knowledge base : {kb.KB_HOME}\nPDF library    : {kb.PAPERS_DIR}\n"
          f"Export         : {kb.EXPORT_DIR or '(none)'}\nModel          : {kb.MODEL_NAME}",
          flush=True)
    if not kb.PAPERS_DIR or not os.path.isdir(kb.PAPERS_DIR):
        print(f"ERROR: the PDF library folder is not set or not found: {kb.PAPERS_DIR!r}\n"
              f"Set papers_dir in {kb.CONFIG_PATH} (or Options in the panel).", flush=True)
        return 2

    rc = 0
    try:
        if args.mode == "post":
            post_steps()
            return 0

        if args.mode == "rebuild":
            reset = ["--yes"] + (["--clear-text-cache"] if args.clear_text_cache else [])
            run(module("reset", *reset), "back up, then clear catalogue and notes", fatal=True)

        if not args.no_lm_setup:
            lm_setup()
        run(module("scouts"), "refresh scout index")
        # Before spending model time: drop incoming PDFs that duplicate a
        # paper already catalogued (same DOI and first page; larger file wins).
        run(module("dedup", "--apply", "--pending"), "check incoming duplicates")

        upd = []
        if args.limit:
            upd += ["--limit", str(args.limit)]
        if args.max_minutes:
            upd += ["--max-minutes", str(args.max_minutes)]
        for flag in ("skip_supplementary", "retry_failed", "no_vision"):
            if getattr(args, flag):
                upd.append("--" + flag.replace("_", "-"))
        if args.system_prompt_file:
            upd += ["--system-prompt-file", args.system_prompt_file]
        rc = run(module("ingest", *upd), "ingest new papers")

        post_steps()
        return rc
    finally:
        # Publish whatever is committed, even after a failure or a Stop.
        if kb.EXPORT_DIR:
            run(module("export"), "export to synced folder")
        print(f"\n=== pipeline finished in {(time.time() - t0) / 60:.1f} min ===", flush=True)


if __name__ == "__main__":
    raise SystemExit(main())
