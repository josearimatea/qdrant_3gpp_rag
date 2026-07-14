"""Type definitions for qdrant_3gpp_rag."""

from dataclasses import dataclass, field
from typing import List

from langchain_core.documents import Document


@dataclass
class RetrievalResult:
    """Result of a retrieval operation.

    Attributes:
        docs: Retrieved documents.
        generated_query: The query used for retrieval (may differ from input
            when SelfQueryRetriever rewrites it).
        generated_filter: Human-readable description of the filter applied.
    """

    docs: List[Document] = field(default_factory=list)
    generated_query: str = ""
    generated_filter: str = ""
