#!/usr/bin/env python3
"""
CLI to index all 3GPP specs from a TSpec-LLM directory tree into Qdrant.

Run once to build a persistent collection consumers can query. Heavy: embedding
a whole release is many thousands of chunks.

Usage (example run from the package workspace `qdrant_3gpp_rag`):
    python scripts/index_3gpp_all.py \
        --docs-dir ../../Dataset/TSpec-LLM/3GPP-clean \
        --collection 3gpp_rel18_all \
        --release Rel-18 \
        --device cuda \
        --output-dir output \
        --force-chunks

The script caches chunk segments in the `output/` folder by default, using a
cache file named after the target collection. Add `--force-chunks` to rebuild
chunks from source even if the cache already exists.

The default dataset path used in examples is exposed in
`qdrant_3gpp_rag.config.paths.DOCS_DIR` — import it if you need the
pre-configured relative path, for example:

    python -c "from qdrant_3gpp_rag.config.paths import DOCS_DIR; print(DOCS_DIR)"

Omit --release/--series to index everything under --docs-dir.

Incremental (only what changed; each chunk's point id comes from its content):
    python scripts/index_3gpp_all.py ... --no-recreate --prune --dry-run   # report
    python scripts/index_3gpp_all.py ... --no-recreate --prune             # apply

One spec only, in any version, leaving the rest of the collection as it is:
    python scripts/index_3gpp_all.py ... --replace-spec 28532 --dry-run    # report
    python scripts/index_3gpp_all.py ... --replace-spec 28532              # apply
"""

import argparse
import logging
import socket
import sys

from qdrant_3gpp_rag.factory import DEFAULT_DEVICE
from qdrant_3gpp_rag.index_3gpp_all import index_3gpp_all

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")


def check_qdrant_server(host: str, port: int, timeout: float = 5.0) -> None:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.settimeout(timeout)
        err = sock.connect_ex((host, port))
        if err != 0:
            raise ConnectionError(
                f"Qdrant is not reachable at {host}:{port}. "
                "Start Qdrant and try again."
            )


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
    parser.add_argument("--spec", action="append", default=None,
                        help="Restrict to a spec file stem (repeatable, e.g. --spec 28532-i00).")
    parser.add_argument("--device", default=DEFAULT_DEVICE,
                        help="Embedding device: cuda | cpu (default: the GPU when there is one).")
    parser.add_argument("--qdrant-host", default="localhost", help="Qdrant host.")
    parser.add_argument("--qdrant-port", type=int, default=6333, help="Qdrant port.")
    parser.add_argument("--output-dir", default="output",
                        help="Directory to cache chunk segments for the collection.")
    parser.add_argument("--force-chunks", action="store_true",
                        help="Rebuild chunk cache even if it already exists.")
    parser.add_argument("--no-recreate", action="store_true",
                        help="Keep the collection and embed only the chunks not yet in it.")
    parser.add_argument("--prune", action="store_true",
                        help="With --no-recreate, also delete the points no longer produced.")
    parser.add_argument("--dry-run", action="store_true",
                        help="Only report what would be added / kept / deleted.")
    parser.add_argument("--replace-spec", default=None,
                        help="Spec number: replace only that spec's points with what --docs-dir "
                             "holds of it (e.g. 28532); the rest of the collection stays.")
    args = parser.parse_args()

    try:
        check_qdrant_server(args.qdrant_host, args.qdrant_port)
        n = index_3gpp_all(
            args.docs_dir,
            args.collection,
            device=args.device,
            qdrant_host=args.qdrant_host,
            qdrant_port=args.qdrant_port,
            releases=args.release,
            series=args.series,
            specs=args.spec,
            output_dir=args.output_dir,
            force_chunk_rebuild=args.force_chunks,
            recreate=not args.no_recreate,
            prune=args.prune,
            dry_run=args.dry_run,
            replace_spec=args.replace_spec,
        )
        verb = "Would write" if args.dry_run else "Wrote"
        print(f"{verb} {n} chunk(s) into collection '{args.collection}'.")
    except Exception as e:
        print(f"ERROR: {e}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
