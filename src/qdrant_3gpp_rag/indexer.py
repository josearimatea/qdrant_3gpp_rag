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

import logging
import re
from typing import Any, Dict, List, Optional

from langchain_text_splitters import MarkdownHeaderTextSplitter, RecursiveCharacterTextSplitter
from qdrant_client.http.models import (
    Distance,
    Modifier,
    PayloadSchemaType,
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
KEYWORD_INDEXED_FIELDS = ("spec", "clause", "clause_ancestors", "defines", "mentions")

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
) -> int:
    """Chunk and index a list of segments, each carrying its own metadata.

    Unlike index_document (one metadata for the whole text), this indexes many
    segments — e.g. one per document section — so each chunk keeps the metadata
    of the segment it came from (plus chunk_index). This enables metadata-filtered
    retrieval, such as restricting search to one service or stage.

    Args:
        segments: list of dicts. Each must hold the chunkable text under
            ``text_key``; all other keys become that segment's chunk metadata.
        factory: QdrantFactory instance.
        collection_name: target collection.
        text_key: key in each segment holding the text (default "text").
        chunk_size, chunk_overlap, embedding_dim, recreate: as in index_document.
            chunk_size=None indexes each segment as ONE chunk — for segments already
            cut (spec_sections.chunk_spec_sections: one per section).
        hybrid: create the collection with a BM25 sparse vector ("text-sparse",
            IDF applied by Qdrant) besides the dense one, and write both;
            retrieval then fuses the two rankings (factory.get_vector_store).

    Returns:
        Total number of chunks indexed across all segments.

    Raises:
        ValueError: If segments is empty or yields no chunk.
    """
    if not segments:
        raise ValueError("Cannot index an empty segment list.")

    all_chunks: List[str] = []
    all_metadatas: List[Dict[str, Any]] = []
    for seg in segments:
        text = (seg.get(text_key) or "").strip()
        if not text:
            continue
        base = {k: v for k, v in seg.items() if k != text_key}
        chunks = ([text] if chunk_size is None else
                  chunk_text(text, chunk_size=chunk_size, chunk_overlap=chunk_overlap))
        for i, chunk in enumerate(chunks):
            all_chunks.append(chunk)
            all_metadatas.append({**base, "chunk_index": i})

    if not all_chunks:
        raise ValueError("Segments produced no indexable text.")

    logger.info(
        "Chunked %d segment(s) into %d chunk(s) for '%s'.",
        len(segments), len(all_chunks), collection_name,
    )

    client = factory.client
    if recreate and client.collection_exists(collection_name):
        client.delete_collection(collection_name)
    if not client.collection_exists(collection_name):
        client.create_collection(
            collection_name=collection_name,
            vectors_config={
                DEFAULT_VECTOR_NAME: VectorParams(size=embedding_dim, distance=Distance.COSINE)
            },
            sparse_vectors_config=(
                {DEFAULT_SPARSE_VECTOR_NAME: SparseVectorParams(modifier=Modifier.IDF)}
                if hybrid else None),
        )
        for name in KEYWORD_INDEXED_FIELDS:
            client.create_payload_index(collection_name, field_name=f"metadata.{name}",
                                        field_schema=PayloadSchemaType.KEYWORD)
        logger.info("Created collection '%s' (dim=%d, hybrid=%s).",
                    collection_name, embedding_dim, hybrid)

    # AUTO: writes both vectors when the collection has the sparse one (a kept,
    # dense-only collection with recreate=False stays dense-only).
    vector_store = factory.get_vector_store(collection_name, sparse_vector_name=AUTO)
    vector_store.add_texts(texts=all_chunks, metadatas=all_metadatas)

    total = client.count(collection_name).count
    logger.info("Indexing complete — %d vector(s) in '%s'.", total, collection_name)
    return len(all_chunks)


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
