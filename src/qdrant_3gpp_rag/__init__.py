"""
qdrant_3gpp_rag — Shared Qdrant/RAG retrieval package for 3GPP projects.

Quick start:
    from qdrant_3gpp_rag import QdrantFactory, retrieve, get_relevant_documents

    factory = QdrantFactory.get_instance(device="cuda")
    result = retrieve("createMOI input parameters",
                      factory=factory, collection_name="3gpp", llm=my_llm)
    print(result.docs)
"""

from qdrant_3gpp_rag._types import RetrievalResult
from qdrant_3gpp_rag.factory import QdrantFactory
from qdrant_3gpp_rag.index_3gpp_all import (
    build_3gpp_segments,
    index_3gpp_all,
    load_3gpp_specs,
)
from qdrant_3gpp_rag.indexer import (
    chunk_text,
    drop_collection,
    index_document,
    index_segments,
)
from qdrant_3gpp_rag.retriever import (
    build_self_query_filters,
    format_document,
    get_relevant_chunks,
    get_relevant_documents,
    retrieve,
)
from qdrant_3gpp_rag.self_query import (
    DEFAULT_DOCUMENT_CONTENT_DESCRIPTION,
    DEFAULT_METADATA_FIELD_INFO,
)

__all__ = [
    "QdrantFactory",
    "RetrievalResult",
    "build_3gpp_segments",
    "build_self_query_filters",
    "chunk_text",
    "drop_collection",
    "index_3gpp_all",
    "load_3gpp_specs",
    "format_document",
    "get_relevant_chunks",
    "get_relevant_documents",
    "index_document",
    "index_segments",
    "retrieve",
    "DEFAULT_METADATA_FIELD_INFO",
    "DEFAULT_DOCUMENT_CONTENT_DESCRIPTION",
]
