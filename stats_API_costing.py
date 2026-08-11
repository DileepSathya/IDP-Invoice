#!/usr/bin/env python3
"""
gemini_metrics_stats.py
------------------------
Reads the `Gemini_metrics` MongoDB collection (database: IDP) and prints a
usage report:

  - Total documents / date range covered
  - Unique sources (with doc counts)
  - Unique models (with doc counts)
  - Status breakdown (success / error / etc.)
  - Token usage stats (sum / avg / min / max) overall, for:
        prompt_token_count, candidates_token_count, total_token_count,
        thoughts_token_count, cached_content_token_count
  - Token usage broken down PER MODEL
  - Token usage broken down PER SOURCE
  - Latency stats

Requirements:
    pip install pymongo

Usage:
    python gemini_metrics_stats.py --uri "mongodb://localhost:27017" \
        --db IDP --collection Gemini_metrics

    # or set the connection string via env var instead of --uri:
    export MONGO_URI="mongodb+srv://user:pass@cluster.mongodb.net"
    python gemini_metrics_stats.py

Optional filters:
    --start-date 2026-08-01   (filters on started_at, inclusive)
    --end-date   2026-08-05
    --source ocr
    --model gemini-3.5-flash-lite
    --json report.json        (also dump the raw report as JSON)
"""

import argparse
import json
import os
import sys
from datetime import datetime, timezone

try:
    from pymongo import MongoClient
except ImportError:
    sys.exit("pymongo is required. Install it with: pip install pymongo")


TOKEN_FIELDS = [
    "prompt_token_count",
    "candidates_token_count",
    "total_token_count",
    "thoughts_token_count",
    "cached_content_token_count",
]


def parse_args():
    p = argparse.ArgumentParser(description="Gemini_metrics stats report")
    p.add_argument("--uri", default=os.environ.get("MONGO_URI", "mongodb://localhost:27017"),
                    help="MongoDB connection string (default: $MONGO_URI or localhost)")
    p.add_argument("--db", default="IDP", help="Database name (default: IDP)")
    p.add_argument("--collection", default="Gemini_metrics", help="Collection name (default: Gemini_metrics)")
    p.add_argument("--start-date", help="Filter: started_at >= YYYY-MM-DD")
    p.add_argument("--end-date", help="Filter: started_at <= YYYY-MM-DD")
    p.add_argument("--source", help="Filter: only this source")
    p.add_argument("--model", help="Filter: only this model")
    p.add_argument("--top", type=int, default=20, help="Max rows to show per breakdown table (default: 20)")
    p.add_argument("--json", metavar="PATH", help="Also write the raw aggregated report to this JSON file")
    return p.parse_args()


def build_match_filter(args):
    match = {}
    if args.source:
        match["source"] = args.source
    if args.model:
        match["model"] = args.model
    date_filter = {}
    if args.start_date:
        date_filter["$gte"] = datetime.strptime(args.start_date, "%Y-%m-%d").replace(tzinfo=timezone.utc)
    if args.end_date:
        date_filter["$lte"] = datetime.strptime(args.end_date, "%Y-%m-%d").replace(tzinfo=timezone.utc)
    if date_filter:
        match["started_at"] = date_filter
    return match


def usage_agg_fields(prefix=""):
    """Build the group-stage accumulator fields for all token metrics."""
    fields = {}
    for f in TOKEN_FIELDS:
        path = f"$usage.{f}"
        fields[f"{f}_sum"] = {"$sum": path}
        fields[f"{f}_avg"] = {"$avg": path}
        fields[f"{f}_min"] = {"$min": path}
        fields[f"{f}_max"] = {"$max": path}
        fields[f"{f}_count"] = {
            "$sum": {"$cond": [{"$eq": [{"$ifNull": [path, None]}, None]}, 0, 1]}
        }
    return fields


def run_report(coll, match):
    pipeline = [
        {"$match": match} if match else {"$match": {}},
        {
            "$facet": {
                "total_docs": [{"$count": "count"}],
                "date_range": [
                    {"$group": {
                        "_id": None,
                        "min_start": {"$min": "$started_at"},
                        "max_start": {"$max": "$started_at"},
                    }}
                ],
                "unique_sources": [
                    {"$group": {"_id": "$source", "count": {"$sum": 1}}},
                    {"$sort": {"count": -1}},
                ],
                "unique_models": [
                    {"$group": {"_id": "$model", "count": {"$sum": 1}}},
                    {"$sort": {"count": -1}},
                ],
                "status_breakdown": [
                    {"$group": {"_id": "$status", "count": {"$sum": 1}}},
                    {"$sort": {"count": -1}},
                ],
                "usage_overall": [
                    {"$group": {"_id": None, **usage_agg_fields()}}
                ],
                "usage_by_model": [
                    {"$group": {"_id": "$model", "docs": {"$sum": 1}, **usage_agg_fields()}},
                    {"$sort": {"docs": -1}},
                ],
                "usage_by_source": [
                    {"$group": {"_id": "$source", "docs": {"$sum": 1}, **usage_agg_fields()}},
                    {"$sort": {"docs": -1}},
                ],
                "latency": [
                    {"$group": {
                        "_id": None,
                        "avg_latency": {"$avg": "$latency_seconds"},
                        "min_latency": {"$min": "$latency_seconds"},
                        "max_latency": {"$max": "$latency_seconds"},
                    }}
                ],
            }
        },
    ]
    result = list(coll.aggregate(pipeline))
    return result[0] if result else {}


# ---------- formatting helpers ----------

def fmt_num(n, decimals=0):
    if n is None:
        return "N/A"
    if decimals:
        return f"{n:,.{decimals}f}"
    return f"{n:,.0f}"


def print_header(title):
    print("\n" + "=" * 78)
    print(title)
    print("=" * 78)


def print_table(rows, headers, widths):
    fmt_row = "  ".join(f"{{:<{w}}}" for w in widths)
    print(fmt_row.format(*headers))
    print(fmt_row.format(*["-" * w for w in widths]))
    for r in rows:
        print(fmt_row.format(*[str(c) for c in r]))


def print_usage_block(usage_doc, docs_count=None):
    if not usage_doc:
        print("  (no usage data)")
        return
    headers = ["Metric", "Sum", "Avg", "Min", "Max", "Non-null docs"]
    widths = [28, 14, 12, 10, 10, 14]
    rows = []
    for f in TOKEN_FIELDS:
        rows.append([
            f,
            fmt_num(usage_doc.get(f"{f}_sum")),
            fmt_num(usage_doc.get(f"{f}_avg"), 1),
            fmt_num(usage_doc.get(f"{f}_min")),
            fmt_num(usage_doc.get(f"{f}_max")),
            fmt_num(usage_doc.get(f"{f}_count")),
        ])
    print_table(rows, headers, widths)
    if docs_count is not None:
        print(f"  (based on {docs_count:,} documents)")


def print_report(report, top_n):
    total_docs = 0
    if report.get("total_docs"):
        total_docs = report["total_docs"][0]["count"]

    print_header("GEMINI_METRICS REPORT")
    print(f"Total documents matched: {total_docs:,}")

    if report.get("date_range"):
        dr = report["date_range"][0]
        print(f"Date range (started_at): {dr.get('min_start')}  ->  {dr.get('max_start')}")

    # Unique sources
    print_header("UNIQUE SOURCES")
    sources = report.get("unique_sources", [])
    print(f"Total unique sources: {len(sources)}")
    rows = [[s["_id"] if s["_id"] is not None else "(null)", f'{s["count"]:,}'] for s in sources[:top_n]]
    print_table(rows, ["Source", "Doc Count"], [40, 12])

    # Unique models
    print_header("UNIQUE MODELS")
    models = report.get("unique_models", [])
    print(f"Total unique models: {len(models)}")
    rows = [[m["_id"] if m["_id"] is not None else "(null)", f'{m["count"]:,}'] for m in models[:top_n]]
    print_table(rows, ["Model", "Doc Count"], [40, 12])

    # Status breakdown
    print_header("STATUS BREAKDOWN")
    statuses = report.get("status_breakdown", [])
    rows = [[s["_id"] if s["_id"] is not None else "(null)", f'{s["count"]:,}'] for s in statuses]
    print_table(rows, ["Status", "Doc Count"], [20, 12])

    # Overall usage
    print_header("TOKEN USAGE - OVERALL")
    overall = report.get("usage_overall", [{}])
    overall = overall[0] if overall else {}
    print_usage_block(overall, docs_count=total_docs)

    # Latency
    if report.get("latency"):
        lat = report["latency"][0]
        print_header("LATENCY (seconds)")
        print(f"  Avg: {fmt_num(lat.get('avg_latency'), 2)}   "
              f"Min: {fmt_num(lat.get('min_latency'), 2)}   "
              f"Max: {fmt_num(lat.get('max_latency'), 2)}")

    # Usage by model
    print_header(f"TOKEN USAGE - BY MODEL (top {top_n})")
    for m in report.get("usage_by_model", [])[:top_n]:
        name = m["_id"] if m["_id"] is not None else "(null)"
        print(f"\n--- Model: {name}  ({m['docs']:,} docs) ---")
        print_usage_block(m)

    # Usage by source
    print_header(f"TOKEN USAGE - BY SOURCE (top {top_n})")
    for s in report.get("usage_by_source", [])[:top_n]:
        name = s["_id"] if s["_id"] is not None else "(null)"
        print(f"\n--- Source: {name}  ({s['docs']:,} docs) ---")
        print_usage_block(s)


def main():
    args = parse_args()
    client = MongoClient(args.uri)
    coll = client[args.db][args.collection]

    match = build_match_filter(args)
    if match:
        print(f"Applying filter: {match}")

    report = run_report(coll, match)
    print_report(report, args.top)

    if args.json:
        with open(args.json, "w") as f:
            json.dump(report, f, default=str, indent=2)
        print(f"\nRaw report written to {args.json}")

    client.close()


if __name__ == "__main__":
    main()