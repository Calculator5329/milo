"""Command line interface for the Milo freshness index."""

from __future__ import annotations

import argparse
import json

from .ingest import ingest
from .search import search, status


def main(argv=None):
    parser = argparse.ArgumentParser(description="Build and query Milo's recent-information index.")
    subparsers = parser.add_subparsers(dest="command", required=True)
    subparsers.add_parser("ingest", help="fetch enabled sources and update the index")
    search_parser = subparsers.add_parser("search", help="search indexed recent information")
    search_parser.add_argument("query")
    search_parser.add_argument("--limit", type=int, default=4)
    subparsers.add_parser("status", help="show index freshness and source coverage")
    args = parser.parse_args(argv)
    if args.command == "ingest":
        result = ingest()
    elif args.command == "search":
        result = search(args.query, limit=args.limit)
    else:
        result = status()
    print(json.dumps(result, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
