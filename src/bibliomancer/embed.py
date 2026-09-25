"""
kb_embed.py - Semantic index over the notes, via the local embedding model.

Each paper is embedded from the fields that carry its meaning - title, fields,
keywords, research question, key findings and core takeaway - using the
nomic-embed-text model already installed in LM Studio (768 dims, 84 MB).

Incremental: a paper is re-embedded only when the text it is embedded from
changes, and rows for papers no longer in the catalogue are dropped. A full
build of ~500 notes takes a few minutes; a weekly update, seconds.

Storage (cache/embeddings/):
    vectors.npy   float32, L2-normalised, one row per paper
    index.json    row order: [{file_hash, text_sha}]

nomic-embed requires task prefixes: "search_document: " for what is stored and
"search_query: " for what is asked. Without them retrieval quality drops.

    python kb_embed.py            # update the index
    python kb_embed.py --rebuild  # re-embed everything
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os

from . import core as kb

VEC_PATH = os.path.join(kb.EMBED_DIR, "vectors.npy")
IDX_PATH = os.path.join(kb.EMBED_DIR, "index.json")
BATCH = 32
MAX_CHARS = 5000          # nomic context is 2048 tokens; stay well inside it


def paper_text(row) -> str:
    def js(v):
        try:
            return json.loads(v or "[]")
        except Exception:
            return []
    parts = [
        row["title"] or "",
        "Fields: " + ", ".join(js(row["research_fields"])),
        "Keywords: " + ", ".join(js(row["keywords"])),
        row["research_question"] or "",
        " ".join(js(row["key_findings"])),
        row["core_takeaway"] or "",
    ]
    return "\n".join(p for p in parts if p.strip())[:MAX_CHARS]


def embed(texts: list[str], prefix: str) -> list[list[float]]:
    import requests
    r = requests.post(f"{kb.LM_BASE}/embeddings",
                      json={"model": kb.EMBED_MODEL, "input": [prefix + t for t in texts]},
                      timeout=600)
    r.raise_for_status()
    data = sorted(r.json()["data"], key=lambda d: d["index"])
    return [d["embedding"] for d in data]


def load_index():
    import numpy as np
    try:
        with open(IDX_PATH, encoding="utf-8") as fh:
            idx = json.load(fh)
        vecs = np.load(VEC_PATH)
        if len(idx) == vecs.shape[0]:
            return idx, vecs
    except (OSError, ValueError):
        pass
    return [], None


def update(rebuild: bool = False, log=print) -> int:
    import numpy as np

    os.makedirs(kb.EMBED_DIR, exist_ok=True)
    conn = kb.connect()
    rows = conn.execute("SELECT * FROM processed_papers").fetchall()
    conn.close()

    old_idx, old_vecs = ([], None) if rebuild else load_index()
    old = {e["file_hash"]: (e["text_sha"], i) for i, e in enumerate(old_idx)}

    keep_idx, keep_vecs, todo = [], [], []
    for r in rows:
        text = paper_text(r)
        sha = hashlib.sha1(text.encode("utf-8")).hexdigest()
        prev = old.get(r["file_hash"])
        if prev and prev[0] == sha and old_vecs is not None:
            keep_idx.append({"file_hash": r["file_hash"], "text_sha": sha})
            keep_vecs.append(old_vecs[prev[1]])
        else:
            todo.append((r["file_hash"], sha, text))

    dropped = len(old_idx) - len(keep_idx) - sum(1 for h, _, _ in todo if h in old)
    log(f"Embeddings: {len(keep_idx)} unchanged, {len(todo)} to embed, "
        f"{max(dropped, 0)} dropped")

    for i in range(0, len(todo), BATCH):
        chunk = todo[i:i + BATCH]
        vecs = embed([t for _, _, t in chunk], "search_document: ")
        for (h, sha, _), v in zip(chunk, vecs):
            keep_idx.append({"file_hash": h, "text_sha": sha})
            keep_vecs.append(np.asarray(v, dtype=np.float32))
        log(f"  embedded {min(i + BATCH, len(todo))}/{len(todo)}")

    if keep_vecs:
        m = np.vstack(keep_vecs).astype(np.float32)
        m /= np.maximum(np.linalg.norm(m, axis=1, keepdims=True), 1e-12)
        np.save(VEC_PATH, m)
    with open(IDX_PATH, "w", encoding="utf-8") as fh:
        json.dump(keep_idx, fh)
    log(f"Semantic index: {len(keep_idx)} paper(s) in {kb.EMBED_DIR}")
    return len(keep_idx)


class SearchIndex:
    """In-memory copy of the index for repeated queries (Browse and Folders pages).
    Same scoring as PhotoMancer: cosine + a small bonus when a query word
    is literally in the paper's title, fields or keywords."""
    WORD_BONUS = 0.05

    def __init__(self, rows_by_hash: dict | None = None):
        import numpy as np
        from ._shared.embed import Embedder
        idx, vecs = load_index()
        self.keys = [e["file_hash"] for e in idx]
        self.vecs = vecs if vecs is not None else np.zeros((0, 768), np.float32)
        self.embed = Embedder(kb.LM_BASE, kb.EMBED_MODEL, timeout=120)
        rows_by_hash = rows_by_hash or {}
        self.words = [_words(" ".join([rows_by_hash[h]["title"] or "", rows_by_hash[h]["keywords"] or "",
                                       rows_by_hash[h]["research_fields"] or ""]))
                      if h in rows_by_hash else set() for h in self.keys]

    def __len__(self) -> int:
        return len(self.keys)

    def query_vec(self, query: str):
        return self.embed.query(query)

    def scores(self, query: str, q=None):
        import numpy as np
        q = self.query_vec(query) if q is None else q
        sim = self.vecs @ q
        qw = _words(query)
        if qw:
            sim = sim + self.WORD_BONUS * np.array([len(qw & w) / len(qw) for w in self.words],
                                                   dtype=np.float32)
        return sim


def _words(text: str) -> set[str]:
    import re
    return {w for w in re.findall(r"[a-z0-9]+", text.lower()) if len(w) > 2}


def search(query: str, k: int = 10):
    """[(score, file_hash)] best first, or None if the index is unavailable."""
    import numpy as np
    idx, vecs = load_index()
    if vecs is None or not idx:
        return None
    try:
        q = np.asarray(embed([query], "search_query: ")[0], dtype=np.float32)
    except Exception:
        return None
    q /= max(float(np.linalg.norm(q)), 1e-12)
    scores = vecs @ q
    order = np.argsort(-scores)[:k]
    return [(float(scores[i]), idx[i]["file_hash"]) for i in order]


def main() -> int:
    ap = argparse.ArgumentParser(description="Build/update the semantic index.")
    ap.add_argument("--rebuild", action="store_true")
    args = ap.parse_args()
    log = kb.Logger("embed")
    ok, detail = kb.lm_available()
    try:
        update(args.rebuild, log=log)
    except Exception as exc:
        log(f"Embedding skipped: {type(exc).__name__}: {exc}")
        log("Is LM Studio running with the embedding model available? Search "
            "falls back to keyword matching until the index is built.")
        return 0          # never fail a pipeline over the optional index
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
