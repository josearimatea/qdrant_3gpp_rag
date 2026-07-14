"""Default 3GPP metadata schema for self-query retrieval.

These defaults match the Qdrant collections used by openapi_chatbotUI and
openapi_rulesbank. Callers can override by passing their own
metadata_field_info and document_content_description to the retriever.

`AttributeInfo` is a lightweight local dataclass (name + description + type),
so the package no longer depends on the legacy
langchain.chains.query_constructor module (removed in LangChain 1.x). The
retriever reads only `.name` and `.description`.
"""

from dataclasses import dataclass


@dataclass
class AttributeInfo:
    """One filterable metadata field: its name, a human description, and type."""

    name: str
    description: str
    type: str = "string"

DEFAULT_METADATA_FIELD_INFO = [
    AttributeInfo(
        name="release",
        description="3GPP specification release (e.g., 'Rel-18')",
        type="string",
    ),
    AttributeInfo(
        name="series",
        description="Specification series (e.g., '28_series')",
        type="string",
    ),
    AttributeInfo(
        name="spec",
        description="Specification name (e.g., '28532')",
        type="string",
    ),
]

DEFAULT_DOCUMENT_CONTENT_DESCRIPTION = "Processed content of 3GPP specifications"
