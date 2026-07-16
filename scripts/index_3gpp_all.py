#!/usr/bin/env python3
"""
CLI to index all 3GPP specs from a TSpec-LLM directory tree into Qdrant.

Run once to build a persistent collection consumers can query. Heavy: embedding
a whole release is many thousands of chunks.

Usage:
    python scripts/index_3gpp_all.py \
        --docs-dir /path/to/TSpec-LLM/3GPP-clean \
        --collection 3gpp_rel18_all \
        --release Rel-18 \
        --device cuda

Omit --release/--series to index everything under --docs-dir.
"""

import argparse
import logging
import sys

from qdrant_3gpp_rag.index_3gpp_all import index_3gpp_all

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")


def main() -> None:
    parser = argparse.ArgumentParser(description="Index 3GPP specs into a Qdrant collection.")
    parser.add_argument("--docs-dir", required=True,
                        help="Root of the release/series/spec.md tree (e.g. .../3GPP-clean).")
    parser.add_argument("--collection", required=True,
                        help="Target Qdrant collection name (persistent).")
    parser.add_argument("--release", action="append", default=None,
                        help="Restrict to a release folder (repeatable, e.g. --release Rel-18).")
    parser.add_argument("--series", action="append", default=None,
                        help="Restrict to a series folder (repeatable, e.g. --series 28_series).")
    parser.add_argument("--device", default="cpu", help="Embedding device: cpu | cuda.")
    parser.add_argument("--no-recreate", action="store_true",
                        help="Add to an existing collection instead of dropping it first.")
    args = parser.parse_args()

    try:
        n = index_3gpp_all(
            args.docs_dir,
            args.collection,
            device=args.device,
            releases=args.release,
            series=args.series,
            recreate=not args.no_recreate,
        )
        print(f"Indexed {n} chunk(s) into collection '{args.collection}'.")
    except Exception as e:
        print(f"ERROR: {e}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
