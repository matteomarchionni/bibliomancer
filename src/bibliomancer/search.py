"""
Instant CLI retrieval over the manifest.

    bibliomancer search bleaching
    bibliomancer search "climate change" --field
    bibliomancer search foundation --since 2024 --limit 10
    bibliomancer search --recent 15
"""

from __future__ import annotations

import argparse
import json
import os
import sys

from . import core as kb


def show(rows, verbose: bool) -> None:
    for r in rows:
        fields = ", ".join(json.loads(r["research_fields"] or "[]"))
        kws = ", ".join(json.loads(r["keywords"] or "[]"))
        print(f"[{r['year']}] {r['title']}")
        print(f"  Authors:  {r['authors']}")
        print(f"  Path:     {r['rel_path']}")
        print(f"  Note:     Notes/{r['note_filename']}")
        print(f"  Fields:   {fields}")
        print(f"  Keywords: {kws}")
        if verbose:
            print(f"  Takeaway: {r['core_takeaway']}")
        print()


def main() -> int:
    ap = argparse.ArgumentParser(description="Search the papers knowledge base.")
    ap.add_argument("query", nargs="?", help="text to look for")
    ap.add_argument("--field", action="store_true", help="match research_fields only")
    ap.add_argument("--tag", action="store_true", help="match keywords only")
    ap.add_argument("--full", action="store_true", help="also search findings and methodology")
    ap.add_argument("--since", type=int, default=0, help="only papers from this year onwards")
    ap.add_argument("--limit", type=int, default=50)
    ap.add_argument("--recent", type=int, default=0, help="list N most recently ingested papers")
    ap.add_argument("-v", "--verbose", action="store_true", help="include core takeaway")
    ap.add_argument("--semantic", action="store_true",
                    help="rank by meaning (embeddings) instead of substring match; "
                         "falls back to substring match if the index is unavailable")
    args = ap.parse_args()
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

    if not os.path.exists(kb.DB_PATH):
        print("Knowledge base not initialised yet - no manifest.db.")
        return 1

    conn = kb.connect()
    try:
        if args.recent:
            rows = conn.execute(
                "SELECT * FROM processed_papers ORDER BY processed_at DESC LIMIT ?",
                (args.recent,),
            ).fetchall()
            print(f"\n{len(rows)} most recently ingested:\n" + "-" * 70)
            show(rows, args.verbose)
            return 0

        query = args.query or input("Enter keyword, field or title fragment: ")

        if args.semantic:
            from . import embed as kb_embed
            hits = kb_embed.search(query, k=args.limit * 3 if args.since else args.limit)
            if hits is None:
                print("(semantic index unavailable - is LM Studio running? "
                      "falling back to substring match)")
            else:
                by_hash = {r["file_hash"]: r for r in conn.execute(
                    "SELECT * FROM processed_papers")}
                ranked = []
                for score, h in hits:
                    r = by_hash.get(h)
                    if r is None:
                        continue
                    if args.since and (not str(r["year"]).isdigit()
                                       or int(r["year"]) < args.since):
                        continue
                    ranked.append((score, r))
                ranked = ranked[:args.limit]
                print(f"\nTop {len(ranked)} by meaning for '{query}':\n" + "-" * 70)
                for score, r in ranked:
                    print(f"  similarity {score:.3f}")
                    show([r], args.verbose)
                return 0

        term = f"%{query}%"

        if args.field:
            where, params = "research_fields LIKE ?", [term]
        elif args.tag:
            where, params = "keywords LIKE ?", [term]
        elif args.full:
            where = ("keywords LIKE ? OR research_fields LIKE ? OR title LIKE ? "
                     "OR authors LIKE ? OR key_findings LIKE ? OR methodology LIKE ? "
                     "OR core_takeaway LIKE ?")
            params = [term] * 7
        else:
            where = "keywords LIKE ? OR research_fields LIKE ? OR title LIKE ? OR authors LIKE ?"
            params = [term] * 4

        if args.since:
            where = f"({where}) AND year != 'Unknown' AND CAST(year AS INTEGER) >= ?"
            params.append(args.since)

        rows = conn.execute(
            f"SELECT * FROM processed_papers WHERE {where} "
            f"ORDER BY (year = 'Unknown'), year DESC LIMIT ?",
            params + [args.limit],
        ).fetchall()

        print(f"\nFound {len(rows)} match(es) for '{query}':\n" + "-" * 70)
        show(rows, args.verbose)
        return 0
    finally:
        conn.close()


if __name__ == "__main__":
    raise SystemExit(main())
