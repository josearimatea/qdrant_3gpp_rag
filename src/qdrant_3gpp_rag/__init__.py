"""
qdrant_3gpp_rag — Shared Qdrant/RAG retrieval package for 3GPP projects.

Quick start:
    from qdrant_3gpp_rag import QdrantFactory, retrieve, get_relevant_documents

    factory = QdrantFactory.get_instance(device="cuda")
    result = retrieve("createMOI input parameters",
                      factory=factory, collection_name="3gpp", llm=my_llm)
    print(result.docs)

Names are loaded lazily, on first use: the submodules behind them pull torch and
the LangChain stack (seconds), which a caller that only reads the model name must
not pay — e.g. a sibling's settings:
    from qdrant_3gpp_rag.embedding_config import DEFAULT_EMBEDDING_MODEL
"""

import importlib
from typing import Any

# public name -> submodule that defines it
_EXPORTS = {
    "RetrievalResult": "_types",
    "DEFAULT_EMBEDDING_DIM": "embedding_config",
    "DEFAULT_EMBEDDING_MODEL": "embedding_config",
    "DEFAULT_DEVICE": "factory",
    "QdrantFactory": "factory",
    "build_embeddings": "factory",
    "build_3gpp_segments": "index_3gpp_all",
    "index_3gpp_all": "index_3gpp_all",
    "load_3gpp_specs": "index_3gpp_all",
    "chunk_markdown_by_headers": "indexer",
    "chunk_text": "indexer",
    "drop_collection": "indexer",
    "index_document": "indexer",
    "index_segments": "indexer",
    "build_self_query_filters": "retriever",
    "format_document": "retriever",
    "get_relevant_chunks": "retriever",
    "get_relevant_documents": "retriever",
    "retrieve": "retriever",
    "chunk_spec_sections": "spec_sections",
    "DEFAULT_DOCUMENT_CONTENT_DESCRIPTION": "self_query",
    "DEFAULT_METADATA_FIELD_INFO": "self_query",
}

__all__ = list(_EXPORTS)


def __getattr__(name: str) -> Any:
    if name not in _EXPORTS:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    value = getattr(importlib.import_module(f"{__name__}.{_EXPORTS[name]}"), name)
    globals()[name] = value                  # later lookups skip __getattr__
    return value


def __dir__() -> list:
    return sorted(set(globals()) | set(__all__))
