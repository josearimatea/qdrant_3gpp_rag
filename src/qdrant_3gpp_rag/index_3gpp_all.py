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

import logging
import os
import re
from typing import Any, Dict, List, Optional

from bs4 import BeautifulSoup
from markdown import markdown
from langchain_text_splitters import MarkdownHeaderTextSplitter, RecursiveCharacterTextSplitter

from qdrant_3gpp_rag.factory import QdrantFactory
from qdrant_3gpp_rag.indexer import index_segments

logger = logging.getLogger(__name__)

# Chunking parameters — match RAG_3gpp_R18_S28.ipynb.
GPP_CHUNK_SIZE = 500
GPP_CHUNK_OVERLAP = 100
_HEADERS_TO_SPLIT_ON = [("#", "Header 1"), ("##", "Header 2"), ("###", "Header 3")]
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
    release/series/spec metadata on every chunk.
    """
    header_splitter = MarkdownHeaderTextSplitter(
        headers_to_split_on=_HEADERS_TO_SPLIT_ON, strip_headers=False
    )
    char_splitter = RecursiveCharacterTextSplitter(
        chunk_size=GPP_CHUNK_SIZE,
        chunk_overlap=GPP_CHUNK_OVERLAP,
        separators=_SEPARATORS,
    )

    segments: List[Dict[str, Any]] = []
    for doc in specs:
        base = {"release": doc["release"], "series": doc["series"], "spec": doc["spec"]}
        for header_chunk in header_splitter.split_text(doc["processed_text"]):
            for chunk in char_splitter.split_text(header_chunk.page_content):
                segments.append({**base, "text": chunk})
    logger.info("Built %d chunk segment(s) from %d spec(s).", len(segments), len(specs))
    return segments


def index_3gpp_all(
    directory: str,
    collection_name: str,
    *,
    factory: Optional[QdrantFactory] = None,
    device: str = "cpu",
    releases: Optional[List[str]] = None,
    series: Optional[List[str]] = None,
    recreate: bool = True,
) -> int:
    """Load, chunk, and index all 3GPP specs under a directory into Qdrant.

    Indexes every spec found by default; releases/series narrow the scope.

    Args:
        directory: root of the ``release/series/spec.md`` tree.
        collection_name: target Qdrant collection (persistent).
        factory: optional QdrantFactory; created from `device` if omitted.
        device: embedding device when building the factory ("cpu" / "cuda").
        releases / series: optional folder whitelists (e.g. ["Rel-18"]).
        recreate: drop and rebuild the collection if it already exists.

    Returns:
        Number of chunks indexed.
    """
    factory = factory or QdrantFactory.get_instance(device=device)
    specs = load_3gpp_specs(directory, releases=releases, series=series)
    if not specs:
        raise ValueError(f"No .md specs found under '{directory}' for the given filters.")
    segments = build_3gpp_segments(specs)
    return index_segments(
        segments,
        factory=factory,
        collection_name=collection_name,
        chunk_size=GPP_CHUNK_SIZE,
        chunk_overlap=GPP_CHUNK_OVERLAP,
        recreate=recreate,
    )
