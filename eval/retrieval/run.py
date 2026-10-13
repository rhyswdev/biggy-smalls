"""The retrieval check (PLAN section 6.1, layer 2): about a dozen queries phrased as an incident commander
would ask them, run through `search_knowledge` exactly as the agent calls it, reported as recall@1 and mean
reciprocal rank. A sanity check, not a benchmark.

    python -m eval.retrieval.run --bundle <folder> --now <time> [--json]

It also reports how many queries would return nothing if every word had to match, which is the measurement
behind joining the query's words with OR.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import yaml

from smalls.tools import registry, store
from smalls.tools.registry import ToolContext

QUERIES = Path(__file__).with_name("queries.yaml")
K = 5


def load_queries(path: Path = QUERIES) -> list[dict[str, Any]]:
    return yaml.safe_load(path.read_text(encoding="utf-8"))


def context(bundle: Path | str, now: str, tags: bool = False) -> ToolContext:
    built = store.build(bundle, tags=tags)
    ms = built.set_now(now)
    return ToolContext(store=built, now=datetime.fromtimestamp(ms / 1000, timezone.utc), tags=tags)


def check(ctx: ToolContext, queries: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    """Run every query and score it: the rank of the first expected handle among the top five passages."""
    spec = registry.registry(tags=ctx.tags)["search_knowledge"]
    rows = []
    for item in queries or load_queries():
        env = registry.call(spec, {"query": item["query"], "k": K}, ctx)
        ranked = [row.handle for row in env.rows]
        rank = next((n for n, handle in enumerate(ranked, 1) if handle in item["expect"]), None)
        words = re.findall(r"\w+", item["query"])
        all_words = " AND ".join(f'"{word}"' for word in words)
        strict = ctx.store.conn.execute("SELECT count(*) FROM chunk_fts JOIN visible_chunk vc ON vc.chunk_id = chunk_fts.rowid WHERE chunk_fts MATCH ?",
                                        (all_words,)).fetchone()[0]
        rows.append({"query": item["query"], "expect": item["expect"], "top": ranked[:1], "rank": rank, "status": env.status,
                     "hits_if_every_word_had_to_match": strict})
    n = len(rows)
    return {"queries": n, "recall_at_1": round(sum(r["rank"] == 1 for r in rows) / n, 3),
            "mrr": round(sum(1 / r["rank"] for r in rows if r["rank"]) / n, 3), "not_in_top_5": sum(r["rank"] is None for r in rows),
            "empty_if_every_word_had_to_match": sum(r["hits_if_every_word_had_to_match"] == 0 for r in rows), "rows": rows}


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="python -m eval.retrieval.run", description="The retrieval check for search_knowledge.")
    p.add_argument("--bundle", required=True)
    p.add_argument("--now", required=True, help="the replay clock: HH:MM on the bundle's day, or an ISO time")
    p.add_argument("--json", action="store_true")
    args = p.parse_args(argv)
    result = check(context(args.bundle, args.now))
    if args.json:
        print(json.dumps(result, indent=2, ensure_ascii=False))
        return 0
    for row in result["rows"]:
        mark = "1st" if row["rank"] == 1 else f"rank {row['rank']}" if row["rank"] else "not in the top 5"
        print(f"{mark:<18} {row['query']}")
    print(f"\n{result['queries']} queries: recall@1 {result['recall_at_1']}, MRR {result['mrr']}, {result['not_in_top_5']} not in the top 5")
    print(f"{result['empty_if_every_word_had_to_match']} of {result['queries']} would return nothing if every word had to match")
    return 0


if __name__ == "__main__":
    sys.exit(main())
