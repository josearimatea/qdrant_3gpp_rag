"""
Singleton factory for QdrantClient + HuggingFaceEmbeddings.

Usage:
    factory = QdrantFactory.get_instance(device="cuda")
    vector_store = factory.get_vector_store("my_collection")

The singleton is keyed on (host, port, model_name, device) so different
configurations coexist safely (useful in tests).
"""

import logging
import threading
from typing import Dict, Optional, Tuple

from langchain_huggingface import HuggingFaceEmbeddings
from langchain_qdrant import FastEmbedSparse, QdrantVectorStore, RetrievalMode
from qdrant_client import QdrantClient

logger = logging.getLogger(__name__)

# Sentinel for get_vector_store(sparse_vector_name=...): read the collection's own
# configuration and search hybrid only when it holds the sparse vector.
AUTO = "auto"

# Defaults — match both projects' current hardcoded values
DEFAULT_QDRANT_HOST = "localhost"
DEFAULT_QDRANT_PORT = 6333
DEFAULT_QDRANT_TIMEOUT = 120
# The ONE embedding model of the 3GPP corpus and of every sibling repository: a query
# must be embedded by the same model as the chunks it is compared with (768 != 384
# dimensions). Siblings import these instead of pinning their own.
# bge-base-en-v1.5 replaces all-MiniLM-L6-v2: it reads 512 tokens (MiniLM read ~256
# and ignored the rest of a chunk) and is strong on technical English.
DEFAULT_EMBEDDING_MODEL = "BAAI/bge-base-en-v1.5"
DEFAULT_EMBEDDING_DIM = 768
# Instruction the model was trained to see before a QUERY (never before a passage).
QUERY_PREFIXES = {
    "BAAI/bge-base-en-v1.5": "Represent this sentence for searching relevant passages: ",
}


def default_device() -> str:
    """'cuda' when a GPU is available, else 'cpu'."""
    import torch

    return "cuda" if torch.cuda.is_available() else "cpu"


DEFAULT_DEVICE = default_device()
DEFAULT_VECTOR_NAME = "text-dense"
DEFAULT_SPARSE_VECTOR_NAME = "text-sparse"
# BM25 term weights (fastembed); IDF is applied by Qdrant (Modifier.IDF on the
# collection's sparse vector). Catches exact identifiers ("NotifyEvent", "4xx/5xx")
# that a dense model blurs.
DEFAULT_SPARSE_MODEL = "Qdrant/bm25"


def build_embeddings(
    model_name: str = DEFAULT_EMBEDDING_MODEL,
    device: str = DEFAULT_DEVICE,
) -> HuggingFaceEmbeddings:
    """The embeddings every repository should use: normalized vectors, and the
    model's query instruction prepended to queries only (embed_query), not to the
    indexed passages (embed_documents)."""
    encode_kwargs = {"normalize_embeddings": True}
    prefix = QUERY_PREFIXES.get(model_name)
    # query_encode_kwargs replaces encode_kwargs for queries, so it repeats them.
    query_encode_kwargs = {**encode_kwargs, "prompt": prefix} if prefix else {}
    return HuggingFaceEmbeddings(
        model_name=model_name,
        model_kwargs={"device": device},
        encode_kwargs=encode_kwargs,
        query_encode_kwargs=query_encode_kwargs,
    )


class QdrantFactory:
    """Thread-safe singleton factory for Qdrant client and embeddings.

    The singleton is keyed on (host, port, model_name, device).
    Calling get_instance() with the same parameters always returns the
    same object, avoiding redundant HuggingFace model loads.
    """

    _instances: Dict[Tuple, "QdrantFactory"] = {}
    _lock = threading.Lock()

    def __init__(
        self,
        host: str = DEFAULT_QDRANT_HOST,
        port: int = DEFAULT_QDRANT_PORT,
        timeout: int = DEFAULT_QDRANT_TIMEOUT,
        model_name: str = DEFAULT_EMBEDDING_MODEL,
        device: str = DEFAULT_DEVICE,
    ) -> None:
        """Direct construction — prefer get_instance() for singleton behaviour."""
        logger.info(
            "Initializing QdrantFactory (host=%s, port=%s, model=%s, device=%s)",
            host, port, model_name, device,
        )
        self.client = QdrantClient(host=host, port=port, timeout=timeout)
        self.embeddings = build_embeddings(model_name=model_name, device=device)
        self._sparse_embeddings: Optional[FastEmbedSparse] = None
        self._sparse_lock = threading.Lock()

    @property
    def sparse_embeddings(self) -> FastEmbedSparse:
        """The BM25 sparse model, loaded on first use (a dense-only caller never
        pays for it)."""
        if self._sparse_embeddings is None:
            with self._sparse_lock:
                if self._sparse_embeddings is None:
                    logger.info("Loading sparse model %s", DEFAULT_SPARSE_MODEL)
                    self._sparse_embeddings = FastEmbedSparse(model_name=DEFAULT_SPARSE_MODEL)
        return self._sparse_embeddings

    def has_sparse_vector(self, collection_name: str,
                          sparse_vector_name: str = DEFAULT_SPARSE_VECTOR_NAME) -> bool:
        """Whether an existing collection holds the named sparse vector."""
        if not self.client.collection_exists(collection_name):
            return False
        params = self.client.get_collection(collection_name).config.params
        return sparse_vector_name in (params.sparse_vectors or {})

    @classmethod
    def get_instance(
        cls,
        host: str = DEFAULT_QDRANT_HOST,
        port: int = DEFAULT_QDRANT_PORT,
        timeout: int = DEFAULT_QDRANT_TIMEOUT,
        model_name: str = DEFAULT_EMBEDDING_MODEL,
        device: str = DEFAULT_DEVICE,
    ) -> "QdrantFactory":
        """Return (or create) the singleton for the given configuration."""
        key = (host, port, model_name, device)
        if key not in cls._instances:
            with cls._lock:
                if key not in cls._instances:
                    cls._instances[key] = cls(
                        host=host, port=port, timeout=timeout,
                        model_name=model_name, device=device,
                    )
        return cls._instances[key]

    @classmethod
    def reset(cls) -> None:
        """Clear all cached instances. Useful in tests."""
        with cls._lock:
            cls._instances.clear()

    def get_vector_store(
        self,
        collection_name: str,
        vector_name: str = DEFAULT_VECTOR_NAME,
        sparse_vector_name: Optional[str] = AUTO,
    ) -> QdrantVectorStore:
        """Return a QdrantVectorStore for the given collection.

        sparse_vector_name:
          AUTO (default) — hybrid (dense + BM25, fused by RRF) when the collection
              holds the "text-sparse" vector, dense-only otherwise; callers need
              not know how a collection was built;
          None — dense-only;
          a name — hybrid with that sparse vector (e.g. while indexing a new
              hybrid collection).

        This is cheap to call repeatedly — it does NOT reload the models.
        """
        if sparse_vector_name == AUTO:
            sparse_vector_name = (DEFAULT_SPARSE_VECTOR_NAME
                                  if self.has_sparse_vector(collection_name) else None)
        kwargs = dict(
            client=self.client,
            collection_name=collection_name,
            embedding=self.embeddings,
            vector_name=vector_name,
        )
        if sparse_vector_name is not None:
            kwargs.update(sparse_embedding=self.sparse_embeddings,
                          sparse_vector_name=sparse_vector_name,
                          retrieval_mode=RetrievalMode.HYBRID)
        return QdrantVectorStore(**kwargs)
