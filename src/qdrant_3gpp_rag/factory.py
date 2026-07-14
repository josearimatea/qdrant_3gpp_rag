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
from langchain_qdrant import QdrantVectorStore
from qdrant_client import QdrantClient

logger = logging.getLogger(__name__)

# Defaults — match both projects' current hardcoded values
DEFAULT_QDRANT_HOST = "localhost"
DEFAULT_QDRANT_PORT = 6333
DEFAULT_QDRANT_TIMEOUT = 120
DEFAULT_EMBEDDING_MODEL = "sentence-transformers/all-MiniLM-L6-v2"
DEFAULT_DEVICE = "cpu"
DEFAULT_VECTOR_NAME = "text-dense"
DEFAULT_SPARSE_VECTOR_NAME = "text-sparse"


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
        self.embeddings = HuggingFaceEmbeddings(
            model_name=model_name,
            model_kwargs={"device": device},
            encode_kwargs={"normalize_embeddings": True},
        )

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
        sparse_vector_name: Optional[str] = DEFAULT_SPARSE_VECTOR_NAME,
    ) -> QdrantVectorStore:
        """Return a QdrantVectorStore for the given collection.

        Pass sparse_vector_name=None for a dense-only collection (e.g. one built
        by indexer.index_document, which produces no sparse vectors). The default
        keeps the hybrid dense+sparse behaviour used by pre-built collections.

        This is cheap to call repeatedly — it does NOT reload the model.
        """
        kwargs = dict(
            client=self.client,
            collection_name=collection_name,
            embedding=self.embeddings,
            vector_name=vector_name,
        )
        if sparse_vector_name is not None:
            kwargs["sparse_vector_name"] = sparse_vector_name
        return QdrantVectorStore(**kwargs)
