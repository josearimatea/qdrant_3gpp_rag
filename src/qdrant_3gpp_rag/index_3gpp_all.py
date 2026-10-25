"""
Index all 3GPP specs from a TSpec-LLM directory tree into Qdrant.

  - Walk a ``release/series/spec.md`` directory tree.
  - Keep each spec as markdown (headings and tables as written; only heading
    attributes such as ``{#post-4 .H6}`` are dropped).
  - Chunk by SECTION (spec_sections.chunk_spec_sections): one chunk per clause,
    starting with its nearest parents' titles; only a section longer than
    GPP_CHUNK_SIZE is divided, each part repeating its heading. Every chunk carries
    release / series / spec and clause / title / path / annex / part / parts.
    Annexes are NOT indexed (nor the cover page): they hold the spec's finished
    OpenAPI / ASN.1 documents and its change history.
  - Index each chunk as it is (index_segments(..., chunk_size=None)).

Until 10/2026 the specs were rendered to HTML and flattened to text, then cut by
headers of levels 1-3 and by 2000 characters: the flattening dropped the heading
markers, so 81-94% of the chunks were plain 2000-character windows — a chunk ended
one clause and began the next, and tables were cut in two.

index_3gpp_all() indexes EVERY spec under the directory by default; optional
releases= / series= / specs= filters narrow the scope. The heavy work (embedding a
whole release) is meant to run once from the CLI (scripts/index_3gpp_all.py);
consumers only query the resulting collection.
"""

import gzip
import json
import logging
import os
import re
from typing import Any, Dict, List, Optional

from qdrant_3gpp_rag.factory import DEFAULT_DEVICE, QdrantFactory
from qdrant_3gpp_rag.indexer import index_segments
from qdrant_3gpp_rag.spec_sections import CHUNKING, chunk_spec_sections

logger = logging.getLogger(__name__)

# Upper bound of a chunk, in characters: a section longer than this is divided into
# parts (one YAML type definition per part first, then by paragraph). 4000 keeps all
# but the longest ~8% of TS 28.532's sections whole — a type definition table is ~2.5k.
GPP_CHUNK_SIZE = 4000
GPP_CHUNK_OVERLAP = 200
_ATTRS = re.compile(r"[ \t]*\{#[^}\n]*\}[ \t]*$", re.MULTILINE)
# A spec file stem: number (dot optional, part suffix kept) and version, then any
# split suffix — "28532-i00", "38101-1-i30_s00-05", "22261-ib0", "22.890-050(cl)".
_SPEC_STEM = re.compile(r"^(?P<number>\d{2}\.?\d{3}(?:-\d+)?)-(?P<version>[a-z0-9]{3})(?![a-z0-9])")
# A document named after the spec it belongs to, with no version ("36.521-1 8.3.1.1.4 TT",
# a test tolerance analysis in the 36 series).
_SPEC_PREFIX = re.compile(r"^(?P<number>\d{2}\.?\d{3}(?:-\d+)?)(?=[ _])")


def spec_identity(stem: str) -> Dict[str, str]:
    """spec_number ("28541", "38101-1") and version ("i50") of a file stem; version ""
    for a document named after its spec without one, both "" for a name with no spec
    number ("10.2 TT")."""
    m = _SPEC_STEM.match(stem)
    if m:
        return {"spec_number": m.group("number").replace(".", ""), "version": m.group("version")}
    m = _SPEC_PREFIX.match(stem)
    return {"spec_number": m.group("number").replace(".", "") if m else "", "version": ""}


def _clean_markdown(content: str) -> str:
    """The spec as markdown, headings kept: heading attributes ({#id .H6}) dropped and
    runs of blank lines collapsed."""
    return re.sub(r"\n\s*\n+", "\n\n", _ATTRS.sub("", content)).strip()


def load_3gpp_specs(
    directory: str,
    releases: Optional[List[str]] = None,
    series: Optional[List[str]] = None,
    specs: Optional[List[str]] = None,
) -> List[Dict[str, str]]:
    """Load .md specs from a release/series/spec directory tree.

    By default loads every spec found; pass releases/series/specs to restrict
    (specs: file stems, e.g. ["28532-i00"]).

    Args:
        directory: root holding ``<release>/<series>/<spec>.md``.
        releases: optional whitelist of release folder names (e.g. ["Rel-18"]).
        series: optional whitelist of series folder names (e.g. ["28_series"]).

    Returns:
        One dict per spec: {release, series, spec, file, processed_text}.
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
                if not file.endswith(".md") or (specs and file[:-3] not in specs):
                    continue
                with open(os.path.join(series_path, file), encoding="utf-8") as f:
                    content = f.read()
                data.append({
                    "release": release,
                    "series": ser,
                    "spec": file[:-3],
                    "file": f"{release}/{ser}/{file}",
                    "processed_text": _clean_markdown(content),
                })
    logger.info("Loaded %d spec file(s) from '%s'.", len(data), directory)
    return data


def build_3gpp_segments(specs: List[Dict[str, str]]) -> List[Dict[str, Any]]:
    """Chunk loaded specs by section (spec_sections.chunk_spec_sections), keeping on
    every chunk where it comes from: release / series / spec (the file stem, as
    consumers filter on it) / spec_number and version (one spec split over several
    files shares its spec_number) / file (path under the corpus root)."""
    segments: List[Dict[str, Any]] = []
    for doc in specs:
        base = {"release": doc["release"], "series": doc["series"], "spec": doc["spec"],
                **spec_identity(doc["spec"]), "file": doc.get("file", "")}
        segments.extend(chunk_spec_sections(doc["processed_text"], base,
                                            max_chars=GPP_CHUNK_SIZE,
                                            overlap=GPP_CHUNK_OVERLAP))
    logger.info("Built %d section chunk(s) from %d spec(s).", len(segments), len(specs))
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
    device: str = DEFAULT_DEVICE,
    qdrant_host: str = "localhost",
    qdrant_port: int = 6333,
    releases: Optional[List[str]] = None,
    series: Optional[List[str]] = None,
    specs: Optional[List[str]] = None,
    output_dir: str = "output",
    force_chunk_rebuild: bool = False,
    recreate: bool = True,
    prune: bool = False,
    dry_run: bool = False,
) -> int:
    """Load, chunk, and index all 3GPP specs under a directory into Qdrant.

    Indexes every spec found by default; releases/series narrow the scope.

    Args:
        directory: root of the ``release/series/spec.md`` tree.
        collection_name: target Qdrant collection (persistent).
        factory: optional QdrantFactory; created from `device` if omitted.
        device: embedding device when building the factory ("cuda" / "cpu";
            default: the GPU when there is one).
        qdrant_host: Qdrant host.
        qdrant_port: Qdrant port.
        releases / series: optional folder whitelists (e.g. ["Rel-18"]).
        specs: optional spec file stems (e.g. ["28532-i00"]).
        output_dir: directory where chunk cache is stored.
        force_chunk_rebuild: ignore existing cache and rebuild chunks (a cache cut by
            another chunking version is rebuilt anyway).
        recreate: drop and rebuild the collection if it already exists. With
            recreate=False only the chunks not yet in the collection are embedded.
        prune: with recreate=False, also delete the points no longer produced.
        dry_run: only log what would be added / kept / deleted.

    Returns:
        Number of chunks written (or that would be, with dry_run).
    """
    factory = factory or QdrantFactory.get_instance(
        host=qdrant_host,
        port=qdrant_port,
        device=device,
    )

    cache_path = _chunk_cache_path(collection_name, output_dir)
    segments: List[Dict[str, Any]] = []
    if os.path.exists(cache_path) and not force_chunk_rebuild:
        segments = _load_segments_cache(cache_path)
        if segments and segments[0].get("chunking") != CHUNKING:
            logger.info("Cache '%s' was cut by %s, not %s — rebuilding.",
                        cache_path, segments[0].get("chunking"), CHUNKING)
            segments = []
        else:
            logger.info("Loaded %d chunk segment(s) from cache '%s'.", len(segments), cache_path)
    if not segments:
        loaded = load_3gpp_specs(directory, releases=releases, series=series, specs=specs)
        if not loaded:
            raise ValueError(f"No .md specs found under '{directory}' for the given filters.")
        segments = build_3gpp_segments(loaded)
        _save_segments_cache(cache_path, segments)
        logger.info("Saved %d chunk segment(s) to cache '%s'.", len(segments), cache_path)

    return index_segments(
        segments,
        factory=factory,
        collection_name=collection_name,
        chunk_size=None,                    # already one chunk per section (or part)
        recreate=recreate,
        prune=prune,
        dry_run=dry_run,
    )
