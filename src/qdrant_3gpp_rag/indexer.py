"""
Generic document indexing into Qdrant.

Chunks an arbitrary document and indexes it into a (dense-only) Qdrant
collection, so any spec — not just a pre-built one — can be embedded on the fly
and retrieved via retriever.retrieve(). Pairs with drop_collection() so callers
can use throwaway collections (index at the start of a run, drop at the end).

Usage:
    factory = QdrantFactory.get_instance(device="cuda")
    index_document(text, factory=factory, collection_name="spec_tmp_123",
                   metadata={"release": "Rel-18", "series": "28_series", "spec": "28532"})
    ...
    drop_collection(factory, "spec_tmp_123")
"""

import hashlib
import json
import logging
import re
import uuid
from typing import Any, Dict, List, Optional, Set

from langchain_text_splitters import MarkdownHeaderTextSplitter, RecursiveCharacterTextSplitter
from qdrant_client.http.models import (
    Distance,
    Modifier,
    OptimizersConfigDiff,
    PayloadSchemaType,
    PointIdsList,
    SparseVectorParams,
    VectorParams,
)

from qdrant_3gpp_rag.factory import (
    AUTO,
    DEFAULT_EMBEDDING_DIM,
    DEFAULT_SPARSE_VECTOR_NAME,
    DEFAULT_VECTOR_NAME,
    QdrantFactory,
)

logger = logging.getLogger(__name__)

# Chunking defaults — match the values both projects use today.
DEFAULT_CHUNK_SIZE = 1000
DEFAULT_CHUNK_OVERLAP = 200

# Metadata fields given a keyword payload index on collection creation, so exact
# filters on them (retriever._to_qdrant_filter) stay fast on a large collection.
# langchain_qdrant stores metadata under the "metadata" payload key. A list field
# (clause_ancestors, defines, mentions) matches when any element equals the value.
KEYWORD_INDEXED_FIELDS = ("spec", "spec_number", "clause", "clause_ancestors", "defines",
                          "mentions")
BOOL_INDEXED_FIELDS = ("annex",)
# index_segments: texts per embedding/upsert call, and the server's default
# indexing threshold (KB), restored after a bulk load.
ADD_BATCH_SIZE = 512
DELETE_BATCH_SIZE = 1000
DEFAULT_INDEXING_THRESHOLD = 10000

# Markdown headers used to split structured documents before size-based chunking.
_DEFAULT_HEADERS_TO_SPLIT_ON = [("#", "Header 1"), ("##", "Header 2"), ("###", "Header 3")]

# A named type definition inside an embedded OpenAPI/YAML block: a PascalCase name
# alone on a line ending in ':' (e.g. "NotificationHeader:", "ErrorResponse:").
# 3GPP specs append their schemas as YAML where each type starts this way and its
# properties follow until the next such line. When several types sit under one
# markdown header they must be split here so one type stays one chunk — a size
# split would interleave a type's fields with its neighbours'.
_YAML_TYPE_DEF_RE = re.compile(r'^[A-Z][A-Za-z0-9]+:\s*$', re.MULTILINE)


def _split_yaml_type_defs(block: str) -> Optional[List[str]]:
    """Split a block at YAML type-definition boundaries, one chunk per type.

    Returns the per-type chunks when the block holds two or more type definitions
    (so splitting is meaningful), else None so the caller falls back to its normal
    size-based chunking. Any preamble before the first type name is kept with it.
    """
    starts = [m.start() for m in _YAML_TYPE_DEF_RE.finditer(block)]
    if len(starts) < 2:
        return None
    bounds = [0] + starts[1:] + [len(block)]
    parts = [block[bounds[i]:bounds[i + 1]].strip() for i in range(len(bounds) - 1)]
    return [p for p in parts if p]


def chunk_text(
    text: str,
    chunk_size: int = DEFAULT_CHUNK_SIZE,
    chunk_overlap: int = DEFAULT_CHUNK_OVERLAP,
) -> List[str]:
    """Split text into overlapping chunks with RecursiveCharacterTextSplitter."""
    splitter = RecursiveCharacterTextSplitter(
        chunk_size=chunk_size,
        chunk_overlap=chunk_overlap,
    )
    return splitter.split_text(text)


def chunk_markdown_by_headers(
    text: str,
    chunk_size: int = DEFAULT_CHUNK_SIZE,
    chunk_overlap: int = DEFAULT_CHUNK_OVERLAP,
    separators: Optional[List[str]] = None,
) -> List[str]:
    """Chunk a markdown document by header first, then by size.

    Splits on '#', '##', '###' headers (keeping the heading in each block) so
    section boundaries are respected, then splits any oversized block by
    characters. Better than plain size chunking for structured specs — a section
    like an OpenAPI "Parameter Object" stays whole instead of being cut mid-way.

    Shared by callers that index structured markdown (the 3GPP corpus and the
    OpenAPI reference), so both use one chunking implementation.
    """
    header_splitter = MarkdownHeaderTextSplitter(
        headers_to_split_on=_DEFAULT_HEADERS_TO_SPLIT_ON, strip_headers=False
    )
    char_splitter = RecursiveCharacterTextSplitter(
        chunk_size=chunk_size,
        chunk_overlap=chunk_overlap,
        separators=separators or ["\n\n", "\n", " ", ""],
    )
    chunks: List[str] = []
    for header_block in header_splitter.split_text(text):
        content = header_block.page_content
        # If the block embeds several YAML type definitions, split per type first so
        # each stays whole; otherwise chunk the block by size as before. Either way,
        # a still-oversized piece is passed through the size splitter as a fallback.
        units = _split_yaml_type_defs(content) or [content]
        for unit in units:
            if len(unit) <= chunk_size:
                chunks.append(unit)
            else:
                chunks.extend(char_splitter.split_text(unit))
    return chunks


def index_document(
    text: str,
    *,
    factory: QdrantFactory,
    collection_name: str,
    metadata: Optional[Dict[str, str]] = None,
    chunk_size: int = DEFAULT_CHUNK_SIZE,
    chunk_overlap: int = DEFAULT_CHUNK_OVERLAP,
    embedding_dim: int = DEFAULT_EMBEDDING_DIM,
    recreate: bool = True,
) -> int:
    """Chunk and index a document into a dense-only Qdrant collection.

    Each chunk is stored with the given metadata plus a per-chunk ``chunk_index``,
    so retrieved documents carry the same fields format_document() expects
    (release / series / spec / chunk_index).

    The collection is created with a single named dense vector ("text-dense"),
    matching factory.get_vector_store(..., sparse_vector_name=None). No sparse
    vectors are produced — retrieval must use dense similarity or SelfQuery.

    Args:
        text: Raw document text to index.
        factory: QdrantFactory instance (use get_instance() for the singleton).
        collection_name: Target collection. Use a throwaway name for temporary
            per-run indexing and drop_collection() afterwards.
        metadata: Base metadata copied onto every chunk (e.g. release/series/spec).
        chunk_size, chunk_overlap: Chunking parameters.
        embedding_dim: Dimension of the embedding model's output vectors.
        recreate: If True (default), drop any existing collection of the same
            name before indexing, so a re-run starts clean.

    Returns:
        Number of chunks indexed.

    Raises:
        ValueError: If text is empty.
    """
    if not text or not text.strip():
        raise ValueError("Cannot index an empty document.")

    base_metadata = dict(metadata or {})
    chunks = chunk_text(text, chunk_size=chunk_size, chunk_overlap=chunk_overlap)
    logger.info("Chunked document into %d chunk(s) for '%s'.", len(chunks), collection_name)

    client = factory.client
    if recreate and client.collection_exists(collection_name):
        client.delete_collection(collection_name)
        logger.debug("Dropped existing collection '%s' before reindex.", collection_name)

    if not client.collection_exists(collection_name):
        client.create_collection(
            collection_name=collection_name,
            vectors_config={
                DEFAULT_VECTOR_NAME: VectorParams(size=embedding_dim, distance=Distance.COSINE)
            },
        )
        logger.info("Created collection '%s' (dim=%d).", collection_name, embedding_dim)

    # Dense-only vector store — no sparse vector for on-the-fly indexing.
    vector_store = factory.get_vector_store(collection_name, sparse_vector_name=None)

    metadatas = [{**base_metadata, "chunk_index": i} for i in range(len(chunks))]
    vector_store.add_texts(texts=chunks, metadatas=metadatas)

    total = client.count(collection_name).count
    logger.info("Indexing complete — %d vector(s) in '%s'.", total, collection_name)
    return len(chunks)


def _point_id(content_hash: str) -> str:
    """A Qdrant point id derived from the chunk's content (a UUID from its hash)."""
    return str(uuid.UUID(content_hash[:32]))


def _content_hash(text: str, metadata: Dict[str, Any]) -> str:
    """sha256 of the chunk's text and metadata: equal only for an identical chunk."""
    blob = json.dumps({"text": text, "metadata": metadata}, sort_keys=True, ensure_ascii=False)
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()


def _existing_ids(client: Any, collection_name: str) -> Set[str]:
    """Every point id in a collection (ids only, no payload or vectors)."""
    ids: Set[str] = set()
    offset = None
    while True:
        points, offset = client.scroll(collection_name, limit=10000, offset=offset,
                                       with_payload=False, with_vectors=False)
        ids.update(str(p.id) for p in points)
        if offset is None:
            return ids


def index_segments(
    segments: List[Dict[str, Any]],
    *,
    factory: QdrantFactory,
    collection_name: str,
    text_key: str = "text",
    chunk_size: Optional[int] = DEFAULT_CHUNK_SIZE,
    chunk_overlap: int = DEFAULT_CHUNK_OVERLAP,
    embedding_dim: int = DEFAULT_EMBEDDING_DIM,
    recreate: bool = True,
    hybrid: bool = True,
    prune: bool = False,
    dry_run: bool = False,
) -> int:
    """Chunk and index a list of segments, each carrying its own metadata.

    Unlike index_document (one metadata for the whole text), this indexes many
    segments — e.g. one per document section — so each chunk keeps the metadata
    of the segment it came from (plus chunk_index). This enables metadata-filtered
    retrieval, such as restricting search to one service or stage.

    Every chunk gets a content_hash (sha256 of its text and metadata) and a point id
    derived from it, so the collection itself tells what is already indexed: with
    recreate=False only the chunks not yet there are embedded and written (a re-run
    writes nothing), and prune=True also deletes the points no longer produced
    (chunks of a changed section, of a removed spec). dry_run=True only logs the
    plan — to add / already indexed / stale — and writes nothing.

    Args:
        segments: list of dicts. Each must hold the chunkable text under
            ``text_key``; all other keys become that segment's chunk metadata.
        factory: QdrantFactory instance.
        collection_name: target collection.
        text_key: key in each segment holding the text (default "text").
        chunk_size, chunk_overlap, embedding_dim: as in index_document.
            chunk_size=None indexes each segment as ONE chunk — for segments already
            cut (spec_sections.chunk_spec_sections: one per section).
        recreate: drop the collection first and index everything (default).
        hybrid: create the collection with a BM25 sparse vector ("text-sparse",
            IDF applied by Qdrant) besides the dense one, and write both;
            retrieval then fuses the two rankings (factory.get_vector_store).
        prune: with recreate=False, delete the points the segments no longer produce
            (use it when `segments` is the collection's whole content).
        dry_run: log what would change and return, writing nothing.

    Returns:
        Number of chunks written (or, with dry_run, that would be written).

    Raises:
        ValueError: If segments is empty or yields no chunk.
    """
    if not segments:
        raise ValueError("Cannot index an empty segment list.")

    chunks: Dict[str, tuple] = {}                 # point id -> (text, metadata)
    for seg in segments:
        text = (seg.get(text_key) or "").strip()
        if not text:
            continue
        base = {k: v for k, v in seg.items() if k != text_key}
        pieces = ([text] if chunk_size is None else
                  chunk_text(text, chunk_size=chunk_size, chunk_overlap=chunk_overlap))
        for i, piece in enumerate(pieces):
            metadata = {**base, "chunk_index": i}
            content_hash = _content_hash(piece, metadata)
            chunks.setdefault(_point_id(content_hash),
                              (piece, {**metadata, "content_hash": content_hash}))

    if not chunks:
        raise ValueError("Segments produced no indexable text.")

    client = factory.client
    exists = client.collection_exists(collection_name)
    existing = _existing_ids(client, collection_name) if exists and not recreate else set()
    to_add = [pid for pid in chunks if pid not in existing]
    stale = sorted(existing - set(chunks)) if prune else []
    logger.info(
        "'%s': %d segment(s) -> %d chunk(s): %d to add, %d already indexed, %d stale%s%s.",
        collection_name, len(segments), len(chunks), len(to_add), len(chunks) - len(to_add),
        len(existing - set(chunks)), " (to delete)" if prune else " (kept)",
        " — recreating the collection" if recreate and exists else "",
    )
    if dry_run:
        return len(to_add)

    if recreate and exists:
        client.delete_collection(collection_name)
    created = False
    if not client.collection_exists(collection_name):
        client.create_collection(
            collection_name=collection_name,
            vectors_config={
                DEFAULT_VECTOR_NAME: VectorParams(size=embedding_dim, distance=Distance.COSINE)
            },
            sparse_vectors_config=(
                {DEFAULT_SPARSE_VECTOR_NAME: SparseVectorParams(modifier=Modifier.IDF)}
                if hybrid else None),
            # Bulk load: no HNSW index while the points arrive; it is built once,
            # below, when indexing is switched back on.
            optimizers_config=OptimizersConfigDiff(indexing_threshold=0),
        )
        created = True
        for name in KEYWORD_INDEXED_FIELDS:
            client.create_payload_index(collection_name, field_name=f"metadata.{name}",
                                        field_schema=PayloadSchemaType.KEYWORD)
        for name in BOOL_INDEXED_FIELDS:
            client.create_payload_index(collection_name, field_name=f"metadata.{name}",
                                        field_schema=PayloadSchemaType.BOOL)
        logger.info("Created collection '%s' (dim=%d, hybrid=%s).",
                    collection_name, embedding_dim, hybrid)

    # AUTO: writes both vectors when the collection has the sparse one (a kept,
    # dense-only collection with recreate=False stays dense-only).
    vector_store = factory.get_vector_store(collection_name, sparse_vector_name=AUTO)
    try:
        if to_add:
            # Large calls let the model sort texts by length (less padding per batch).
            vector_store.add_texts(texts=[chunks[pid][0] for pid in to_add],
                                   metadatas=[chunks[pid][1] for pid in to_add],
                                   ids=to_add, batch_size=ADD_BATCH_SIZE)
        for i in range(0, len(stale), DELETE_BATCH_SIZE):
            client.delete(collection_name,
                          points_selector=PointIdsList(points=stale[i:i + DELETE_BATCH_SIZE]))
    finally:
        if created:                    # build the HNSW index once, now
            client.update_collection(collection_name, optimizers_config=OptimizersConfigDiff(
                indexing_threshold=DEFAULT_INDEXING_THRESHOLD))

    total = client.count(collection_name).count
    logger.info("Indexing complete — %d written, %d deleted, %d vector(s) in '%s'.",
                len(to_add), len(stale), total, collection_name)
    return len(to_add)


def drop_collection(factory: QdrantFactory, collection_name: str) -> bool:
    """Delete a collection if it exists. Returns True if a collection was removed.

    Safe to call on a name that does not exist (returns False). Use to clean up
    a temporary per-run collection created by index_document().
    """
    client = factory.client
    if client.collection_exists(collection_name):
        client.delete_collection(collection_name)
        logger.info("Dropped collection '%s'.", collection_name)
        return True
    logger.debug("drop_collection: '%s' does not exist — nothing to do.", collection_name)
    return False
