"""
bench_quants.py - Benchmark individual quantisations of one LM Studio model.

Why this exists: LM Studio groups quantisations of the same model as
"variants" of a single key (e.g. `qwen/qwen3.5-9b`), and `lms load` gives no
way to address one variant directly. Verified 2026-09-22 - none of these
resolve:

    qwen/qwen3.5-9b@q6_k
    lmstudio-community/Qwen3.5-9B-GGUF/Qwen3.5-9B-Q6_K.gguf
    qwen/qwen3.5-9b@lmstudio-community/.../Qwen3.5-9B-Q6_K.gguf
    <absolute path to the .gguf>

`lms load <key> -y` always auto-selects the variant it prefers for the
hardware (Q4_K_M here). The workaround is to temporarily rename the other
.gguf files so only the wanted one is indexable; LM Studio re-scans the
directory immediately (confirmed: the --estimate-only figure moves from
6.10 GiB for Q4 to 7.71 GiB for Q6).

Renames are instant (same volume) and always undone in a finally block, so an
interruption cannot leave the model store in a broken state.

    python bench_quants.py --from-db 5 --context-length 24576 --max-tokens 8000
"""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
_SRC = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src")
sys.path.insert(0, _SRC)
from bibliomancer import core as kb

GGUF_DIR = os.environ.get(
    "KB_GGUF_DIR",
    os.path.join(os.path.expanduser("~"), ".cache", "lm-studio", "models", "lmstudio-community",
                 "Qwen3.5-9B-GGUF"),
)
MODEL_KEY = os.environ.get("KB_BENCH_KEY", "qwen/qwen3.5-9b")
HIDDEN = ".hidden"

QUANTS = {
    "q4": "Qwen3.5-9B-Q4_K_M.gguf",
    "q6": "Qwen3.5-9B-Q6_K.gguf",
    "q8": "Qwen3.5-9B-Q8_0.gguf",
}


def restore_all(log) -> None:
    """Undo every rename. Safe to call repeatedly."""
    for fname in QUANTS.values():
        hidden = os.path.join(GGUF_DIR, fname + HIDDEN)
        real = os.path.join(GGUF_DIR, fname)
        if os.path.exists(hidden) and not os.path.exists(real):
            os.rename(hidden, real)
            log(f"    restored {fname}")


def hide_all_but(keep: str, log) -> None:
    restore_all(log)
    for fname in QUANTS.values():
        if fname == keep:
            continue
        real = os.path.join(GGUF_DIR, fname)
        if os.path.exists(real):
            os.rename(real, real + HIDDEN)
    time.sleep(4)   # let LM Studio re-index the directory


def main() -> int:
    ap = argparse.ArgumentParser(description="Benchmark each quantisation in turn.")
    ap.add_argument("--from-db", type=int, default=5)
    ap.add_argument("--context-length", type=int, default=kb.CONTEXT_LENGTH)
    ap.add_argument("--max-tokens", type=int, default=kb.MAX_TOKENS)
    ap.add_argument("--only", nargs="*", default=list(QUANTS),
                    help="subset of quant tags to run (default: all)")
    args = ap.parse_args()

    log = kb.Logger("bench_quants")
    here = os.path.dirname(os.path.abspath(__file__))

    missing = [q for q in args.only if q not in QUANTS]
    if missing:
        log(f"Unknown quant tag(s): {missing}. Known: {list(QUANTS)}")
        return 1

    log("=" * 70)
    log(f"Quantisation benchmark: {args.only}")
    log(f"GGUF dir: {GGUF_DIR}")

    try:
        for tag in args.only:
            fname = QUANTS[tag]
            if not os.path.exists(os.path.join(GGUF_DIR, fname)) and \
               not os.path.exists(os.path.join(GGUF_DIR, fname + HIDDEN)):
                log(f"SKIP {tag}: {fname} not on disk")
                continue
            log("-" * 70)
            log(f"QUANT {tag}  ({fname})")
            hide_all_but(fname, log)
            rc = subprocess.run(
                [sys.executable, os.path.join(here, "compare_models.py"),
                 "--models", MODEL_KEY,
                 "--from-db", str(args.from_db),
                 "--context-length", str(args.context_length),
                 "--max-tokens", str(args.max_tokens),
                 "--run", f"cmp_{tag}"],
                cwd=here,
            ).returncode
            log(f"QUANT {tag} finished with exit code {rc}")
    finally:
        log("-" * 70)
        log("Restoring all quantisation files...")
        restore_all(log)
        present = sorted(f for f in os.listdir(GGUF_DIR) if f.endswith(".gguf"))
        log(f"GGUF files present: {present}")

    log("Done. Reports under cache/compare/cmp_<tag>/report.md")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
