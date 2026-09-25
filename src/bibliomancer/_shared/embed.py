"""Semantic search primitives shared by both apps: LM Studio's embedding
endpoint, the 'which hits are relevant' cut and folder ranking.

nomic-embed-text needs task prefixes: "search_document: " for what is stored
and "search_query: " for what is asked; without them retrieval quality drops.
"""

from __future__ import annotations

import os

import numpy as np
import requests

DOC_PREFIX, QUERY_PREFIX = "search_document: ", "search_query: "


class Embedder:
    """LM Studio /v1/embeddings client (the model loads just in time)."""

    def __init__(self, base_url: str, model: str, timeout: float = 600):
        self.base, self.model, self.timeout = base_url.rstrip("/"), model, timeout
        self.session = requests.Session()

    def __call__(self, texts: list[str], prefix: str) -> np.ndarray:
        """L2-normalised float32 rows, one per text."""
        r = self.session.post(f"{self.base}/embeddings",
                              json={"model": self.model, "input": [prefix + t for t in texts]},
                              timeout=self.timeout)
        r.raise_for_status()
        data = sorted(r.json()["data"], key=lambda d: d["index"])
        m = np.asarray([d["embedding"] for d in data], dtype=np.float32)
        return m / np.maximum(np.linalg.norm(m, axis=1, keepdims=True), 1e-12)

    def query(self, text: str) -> np.ndarray:
        return self([text], QUERY_PREFIX)[0]


def relevant(scores: np.ndarray, margin: float = 0.10, floor: float = 0.60) -> np.ndarray:
    """Boolean mask of items close enough to the best match to count as hits.

    nomic cosine scores are relative: unrelated items still score ~0.45-0.55,
    so a hit is anything within `margin` of the best score and above `floor`.
    """
    if not len(scores):
        return np.zeros(0, bool)
    top = float(scores.max())
    return scores >= max(floor, top - margin)


def folder_of(path: str, level: int, root_name: str = "(archive root)") -> str:
    """Folder that groups an item: its own folder (level 0) or the first `level` parts."""
    parts = path.replace("/", os.sep).split(os.sep)[:-1]
    if level > 0:
        parts = parts[:level]
    return os.sep.join(parts) or root_name


def rank_folders(keys: list[str], vecs: np.ndarray, scores: np.ndarray, q: np.ndarray,
                 paths: dict[str, list[str]], level: int = 0,
                 root_name: str = "(archive root)") -> list[dict]:
    """Rank folders for a query.

    keys[i] / vecs[i] / scores[i] describe item i; paths[key] lists the relative
    paths of the files with that content. Folder score = 0.5 * mean of its 3
    best item scores (it contains strong matches) + 0.5 * cosine to the folder
    centroid (the folder as a whole is about the query). Hits = items passing
    `relevant`.
    """
    hit = relevant(scores)
    groups: dict[str, list[int]] = {}
    for i, key in enumerate(keys):
        for p in paths.get(key, []):
            groups.setdefault(folder_of(p, level, root_name), []).append(i)
    out = []
    for folder, ids in groups.items():
        ids = sorted(set(ids), key=lambda i: -scores[i])
        top3 = float(np.mean(scores[ids[:3]]))
        c = vecs[ids].mean(axis=0)
        cen = float(c @ q / max(np.linalg.norm(c), 1e-12))
        out.append({"folder": folder, "score": 0.5 * top3 + 0.5 * cen, "photos": len(ids),
                    "ids": ids, "hits": int(hit[ids].sum()), "best": [keys[i] for i in ids[:6]],
                    "best_scores": [float(scores[i]) for i in ids[:6]]})
    out.sort(key=lambda d: -d["score"])
    return out
