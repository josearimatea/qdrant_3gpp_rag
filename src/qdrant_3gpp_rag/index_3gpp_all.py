"""
Index all 3GPP specs from a TSpec-LLM directory tree into Qdrant.

Replicates the preprocessing used to build the existing 3gpp_rel18_* collections
(see RAG_3gpp_R18_S28.ipynb):

  - Walk a ``release/series/spec.md`` directory tree.
  - For each .md, render markdown to HTML (with tables) and flatten it back to
    readable text via BeautifulSoup, so spec tables survive as plain text.
  - Split by Markdown headers, then by characters (chunk_size=500, overlap=100).
  - Index each chunk into Qdrant with release/series/spec metadata.

index_3gpp_all() indexes EVERY spec under the directory by default; optional
releases= / series= filters narrow the scope. The heavy work (embedding a whole
release) is meant to run once from the CLI (scripts/index_3gpp_all.py); consumers
only query the resulting collection.
"""

import gzip
import json
import logging
import os
import re
from typing import Any, Dict, List, Optional

from bs4 import BeautifulSoup
from markdown import markdown

from qdrant_3gpp_rag.factory import QdrantFactory
from qdrant_3gpp_rag.indexer import chunk_markdown_by_headers, index_segments

logger = logging.getLogger(__name__)

# Chunking parameters — match RAG_3gpp_R18_S28.ipynb.
GPP_CHUNK_SIZE = 500
GPP_CHUNK_OVERLAP = 100
_SEPARATORS = ["\n\n", "\n", " ", ""]


def _flatten_markdown(content: str) -> str:
    """Render markdown (with tables) to HTML then flatten to readable plain text.

    Mirrors the notebook loader: HTML tables become newline-separated text, and
    runs of blank lines are collapsed, so tabular spec content stays legible.
    """
    html = markdown(content, extensions=["tables", "fenced_code", "codehilite"])
    soup = BeautifulSoup(html, "html.parser")
    text = soup.get_text(separator="\n", strip=True)
    return re.sub(r"\n\s*\n+", "\n\n", text).strip()


def load_3gpp_specs(
    directory: str,
    releases: Optional[List[str]] = None,
    series: Optional[List[str]] = None,
) -> List[Dict[str, str]]:
    """Load .md specs from a release/series/spec directory tree.

    By default loads every spec found; pass releases/series to restrict.

    Args:
        directory: root holding ``<release>/<series>/<spec>.md``.
        releases: optional whitelist of release folder names (e.g. ["Rel-18"]).
        series: optional whitelist of series folder names (e.g. ["28_series"]).

    Returns:
        One dict per spec: {release, series, spec, processed_text}.
    """
    data: List[Dict[str, str]] = []
    for release in sorted(os.listdir(directory)):
        release_path = os.path.join(directory, release)
        if not os.path.isdir(release_path) or (releases and release not in releases):
            continue
        for ser in sorted(os.listdir(release_path)):
            series_path = os.path.join(release_path, ser)
            if not os.path.isdir(series_path) or (series and ser not in series):
                continue
            for file in sorted(os.listdir(series_path)):
                if not file.endswith(".md"):
                    continue
                with open(os.path.join(series_path, file), encoding="utf-8") as f:
                    content = f.read()
                data.append({
                    "release": release,
                    "series": ser,
                    "spec": file[:-3],
                    "processed_text": _flatten_markdown(content),
                })
    logger.info("Loaded %d spec file(s) from '%s'.", len(data), directory)
    return data


def build_3gpp_segments(specs: List[Dict[str, str]]) -> List[Dict[str, Any]]:
    """Chunk loaded specs into index_segments-ready segments.

    Splits each spec by Markdown headers, then by characters (500/100), keeping
    release/series/spec metadata on every chunk. Uses the shared
    chunk_markdown_by_headers helper.
    """
    segments: List[Dict[str, Any]] = []
    for doc in specs:
        base = {"release": doc["release"], "series": doc["series"], "spec": doc["spec"]}
        for chunk in chunk_markdown_by_headers(
            doc["processed_text"],
            chunk_size=GPP_CHUNK_SIZE,
            chunk_overlap=GPP_CHUNK_OVERLAP,
            separators=_SEPARATORS,
        ):
            segments.append({**base, "text": chunk})
    logger.info("Built %d chunk segment(s) from %d spec(s).", len(segments), len(specs))
    return segments


def _chunk_cache_path(collection_name: str, output_dir: str) -> str:
    safe_name = collection_name.replace('/', '_').replace(' ', '_')
    return os.path.join(output_dir, f"{safe_name}.chunks.jsonl.gz")


def _ensure_output_dir(output_dir: str) -> None:
    os.makedirs(output_dir, exist_ok=True)


def _save_segments_cache(path: str, segments: List[Dict[str, Any]]) -> None:
    _ensure_output_dir(os.path.dirname(path))
    with gzip.open(path, "wt", encoding="utf-8") as f:
        for segment in segments:
            json.dump(segment, f, ensure_ascii=False)
            f.write("\n")


def _load_segments_cache(path: str) -> List[Dict[str, Any]]:
    segments: List[Dict[str, Any]] = []
    with gzip.open(path, "rt", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            segments.append(json.loads(line))
    return segments


def index_3gpp_all(
    directory: str,
    collection_name: str,
    *,
    factory: Optional[QdrantFactory] = None,
    device: str = "cpu",
    qdrant_host: str = "localhost",
    qdrant_port: int = 6333,
    releases: Optional[List[str]] = None,
    series: Optional[List[str]] = None,
    output_dir: str = "output",
    force_chunk_rebuild: bool = False,
    recreate: bool = True,
) -> int:
    """Load, chunk, and index all 3GPP specs under a directory into Qdrant.

    Indexes every spec found by default; releases/series narrow the scope.

    Args:
        directory: root of the ``release/series/spec.md`` tree.
        collection_name: target Qdrant collection (persistent).
        factory: optional QdrantFactory; created from `device` if omitted.
        device: embedding device when building the factory ("cpu" / "cuda").
        qdrant_host: Qdrant host.
        qdrant_port: Qdrant port.
        releases / series: optional folder whitelists (e.g. ["Rel-18"]).
        output_dir: directory where chunk cache is stored.
        force_chunk_rebuild: ignore existing cache and rebuild chunks.
        recreate: drop and rebuild the collection if it already exists.

    Returns:
        Number of chunks indexed.
    """
    factory = factory or QdrantFactory.get_instance(
        host=qdrant_host,
        port=qdrant_port,
        device=device,
    )

    cache_path = _chunk_cache_path(collection_name, output_dir)
    if os.path.exists(cache_path) and not force_chunk_rebuild:
        segments = _load_segments_cache(cache_path)
        logger.info("Loaded %d chunk segment(s) from cache '%s'.", len(segments), cache_path)
    else:
        specs = load_3gpp_specs(directory, releases=releases, series=series)
        if not specs:
            raise ValueError(f"No .md specs found under '{directory}' for the given filters.")
        segments = build_3gpp_segments(specs)
        _save_segments_cache(cache_path, segments)
        logger.info("Saved %d chunk segment(s) to cache '%s'.", len(segments), cache_path)

    return index_segments(
        segments,
        factory=factory,
        collection_name=collection_name,
        chunk_size=GPP_CHUNK_SIZE,
        chunk_overlap=GPP_CHUNK_OVERLAP,
        recreate=recreate,
    )
