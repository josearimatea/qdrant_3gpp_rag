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
from typing import Dict, List, Optional

from langchain_text_splitters import RecursiveCharacterTextSplitter
from qdrant_client.http.models import Distance, VectorParams

from qdrant_3gpp_rag.factory import QdrantFactory, DEFAULT_VECTOR_NAME

logger = logging.getLogger(__name__)

# Chunking defaults — match the values both projects use today.
DEFAULT_CHUNK_SIZE = 1000
DEFAULT_CHUNK_OVERLAP = 200
# all-MiniLM-L6-v2 output dimension.
DEFAULT_EMBEDDING_DIM = 384


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
