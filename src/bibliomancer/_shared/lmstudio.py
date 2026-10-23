"""LM Studio control through its `lms` command line, for both apps: find it,
start the server, load the model at a known context, list installed models.

Lessons both pipelines learned the hard way
  * `lms load` ADDS an instance instead of replacing a loaded one, and the bare
    model id keeps routing to the first; LM Studio's just-in-time loading uses
    its own default context (8192) -> every long request fails with HTTP 400.
    So: unload everything, then load explicitly at the needed context.
  * The context is SHARED between parallel slots: load at per-request x parallel.
  * An instance that already has enough context per slot is reused (it may be
    serving the other app's run right now - unloading it would kill that run).
  * Talk to 127.0.0.1, never 'localhost' (IPv6 first on Windows: +2 s a request).
"""

from __future__ import annotations

import json
import os
import re
import shutil
from typing import Callable

import requests

from .paths import IS_WIN, run_quiet

DEFAULT_BASE_URL = "http://127.0.0.1:1234/v1"


def find_lms(configured: str = "") -> str:
    """Path of the `lms` command line ('' if LM Studio is not installed)."""
    exe = "lms.exe" if IS_WIN else "lms"
    cands = [configured, os.environ.get("LMS_PATH", ""),
             os.path.join(os.path.expanduser("~"), ".lmstudio", "bin", exe),
             os.path.join(os.path.expanduser("~"), ".cache", "lm-studio", "bin", exe),
             shutil.which("lms") or ""]
    return next((c for c in cands if c and os.path.exists(c)), "")


def installed_models(lms: str, kind: str | None = "llm") -> list[dict] | None:
    """Models downloaded in LM Studio (`lms ls --json`), or None if lms is unavailable."""
    if not lms or not os.path.exists(lms):
        return None
    try:
        models = json.loads(run_quiet([lms, "ls", "--json"], timeout=60).stdout)
    except (OSError, ValueError, Exception):
        return None
    return [m for m in models if kind is None or m.get("type") == kind]


def check_model(lms: str, name: str, context_needed: int, need_vision: bool = False,
                ) -> tuple[bool, str]:
    """Is `name` an installed chat model that fits the job? (ok, message listing
    the installed models when not)."""
    models = installed_models(lms, None)
    if models is None:
        return True, f"cannot list LM Studio models ({lms or 'lms'} missing) - not checked"
    llms = [m for m in models if m.get("type") == "llm"]
    listing = "\n".join(
        f"  - {m['modelKey']}  ({m.get('displayName', '')}, "
        f"{(m.get('quantization') or {}).get('name', '?')}, "
        f"{m.get('sizeBytes', 0) / 1e9:.1f} GB{', vision' if m.get('vision') else ''})"
        for m in llms) or "  (none - download one in LM Studio first)"
    hit = next((m for m in models if m.get("modelKey", "").lower() == name.strip().lower()), None)
    if hit is None:
        return False, f"Model '{name}' is not installed in LM Studio.\nInstalled chat models:\n{listing}"
    if hit.get("type") != "llm":
        return False, (f"'{name}' is an {hit.get('type')} model, not a chat model.\n"
                       f"Installed chat models:\n{listing}")
    if need_vision and not hit.get("vision"):
        return False, (f"'{name}' has no vision support - it cannot see images.\n"
                       f"Installed chat models:\n{listing}")
    ctx = hit.get("maxContextLength") or 0
    if ctx and ctx < context_needed:
        return False, (f"'{name}' supports only {ctx} tokens of context (need {context_needed}).\n"
                       f"Installed chat models:\n{listing}")
    q = (hit.get("quantization") or {}).get("name", "?")
    note = ", vision" if hit.get("vision") else (
        "" if need_vision else "; no vision support, so scanned documents cannot be read")
    return True, f"'{hit['modelKey']}' is installed ({hit.get('displayName', '')}, {q}{note})."


def server_up(base_url: str = DEFAULT_BASE_URL, timeout: float = 4) -> tuple[bool, list[str]]:
    """(reachable, model ids offered by the server)."""
    try:
        r = requests.get(f"{base_url}/models", timeout=timeout)
        r.raise_for_status()
        return True, [m.get("id") for m in r.json().get("data", [])]
    except Exception:
        return False, []


def loaded_models(lms: str) -> list[dict]:
    """Instances currently loaded (`lms ps --json`); [] when unknown."""
    try:
        return json.loads(run_quiet([lms, "ps", "--json"], timeout=30).stdout) or []
    except Exception:
        return []


def run_lms(lms: str, args: list[str], label: str, out: Callable[[str], None] = print) -> int:
    """Run `lms <args>` as a pipeline step: '=== label ===' + the last lines."""
    out(f"=== {label} ===")
    p = run_quiet([lms, *args])
    # progress spinners (Braille frames) and terminal escapes are noise in a log
    text = re.sub(r"[\u2800-\u28ff]", "", p.stdout + p.stderr)
    tail = [ln for ln in text.splitlines() if ln.strip() and "\x1b" not in ln]
    for ln in tail[-3:]:
        out(f"  {ln.strip()}")
    return p.returncode


def start_server(lms: str, out: Callable[[str], None] = print) -> None:
    if lms and os.path.exists(lms):
        run_lms(lms, ["server", "start"], "start LM Studio server", out)


def ensure_embedding(lms: str, base_url: str, model: str, ttl_s: int = 3600,
                     wait_s: float = 45) -> tuple[bool, str]:
    """Make search by meaning possible: start the server if it is down (a fresh
    start, or LM Studio closed) and load the embedding model if it is not loaded.
    Does not rely on just-in-time loading, which can be switched off in LM Studio.
    Loading an embedding model adds an instance and never touches a loaded chat
    model. Blocks up to ~wait_s: call it from a worker thread. (ok, reason)."""
    import time
    up = server_up(base_url, timeout=2)[0]
    if not up:
        if not lms or not os.path.exists(lms):
            return False, "LM Studio is not running (and its lms command was not found)"
        try:
            run_quiet([lms, "server", "start"], timeout=wait_s)
        except Exception:
            pass                            # the wait below decides
        end = time.monotonic() + wait_s
        while not up and time.monotonic() < end:
            time.sleep(1)
            up = server_up(base_url, timeout=2)[0]
        if not up:
            return False, "LM Studio did not start"
    if not lms or not os.path.exists(lms):
        return True, ""                     # server up; rely on just-in-time loading
    want = model.strip().lower()
    if any(want in ((m.get("identifier") or "").lower(), (m.get("modelKey") or "").lower())
           for m in loaded_models(lms)):
        return True, ""
    try:
        rc = run_quiet([lms, "load", model, "--ttl", str(ttl_s), "-y"], timeout=180).returncode
    except Exception:
        rc = -1
    if rc != 0:
        return False, f"could not load the embedding model {model} - is it downloaded in LM Studio?"
    return True, ""


def setup(lms: str, model: str, context_per_request: int, parallel: int = 1, ttl_s: int = 3600,
          need_vision: bool = False, out: Callable[[str], None] = print) -> None:
    """Start the server and make sure `model` is loaded with enough context.
    Raises SystemExit(2) if the model is unusable, (3) if it cannot be loaded."""
    parallel = max(1, int(parallel))
    per, ctx = int(context_per_request), int(context_per_request) * max(1, int(parallel))
    if not lms or not os.path.exists(lms):
        out(f"=== lms not found - assuming LM Studio is already running with {model} ===")
        return
    ok, msg = check_model(lms, model, per, need_vision)
    if not ok:
        out("=== model check ===")
        out(f"ERROR: {msg}\nChoose an installed model in the control panel (Options).")
        raise SystemExit(2)
    run_lms(lms, ["server", "start"], "start LM Studio server", out)
    llms = [m for m in loaded_models(lms) if m.get("type", "llm") == "llm"]
    for m in llms:
        if (m.get("identifier") or m.get("modelKey")) != model:
            continue
        slots = int(m.get("parallel") or m.get("maxParallelPredictions") or 1)
        mctx = int(m.get("contextLength") or 0)
        if len(llms) == 1 and slots >= parallel and mctx // slots >= per:
            out(f"=== model already loaded: {model} at {mctx} context, {slots} parallel ===")
            return
    run_lms(lms, ["unload", "--all"], "unload any loaded model", out)
    rc = run_lms(lms, ["load", model, "--context-length", str(ctx), "--parallel", str(parallel),
                       "--ttl", str(ttl_s), "-y"],
                 f"load {model} at {ctx} context, {parallel} parallel", out)
    if rc != 0:
        out(f"ERROR: could not load {model} (exit {rc})")
        raise SystemExit(3)


def gpu_memory_mb() -> int | None:
    """VRAM in use now (nvidia-smi), or None."""
    try:
        p = run_quiet(["nvidia-smi", "--query-gpu=memory.used", "--format=csv,noheader,nounits"],
                      timeout=10)
        return int(p.stdout.strip().splitlines()[0])
    except Exception:
        return None
