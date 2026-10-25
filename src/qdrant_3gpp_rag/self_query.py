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
        description=(
            "Specification file name with version (e.g., '28532-i00'). Prefer "
            "spec_number unless a version is asked for"
        ),
        type="string",
    ),
    AttributeInfo(
        name="spec_number",
        description="Specification number, any version (e.g., '28532', '29571', '38101-1')",
        type="string",
    ),
    AttributeInfo(
        name="clause",
        description=(
            "Exact clause number of one section (e.g., '12.3.1.2.4.2.1'). Use only when "
            "the query names a precise clause; for a clause and everything under it use "
            "clause_ancestors"
        ),
        type="string",
    ),
    AttributeInfo(
        name="clause_ancestors",
        description=(
            "Restricts the search to a clause and all its sub-clauses (e.g., '12.3' "
            "matches 12.3, 12.3.1, 12.3.1.2.3.2.1.3.1 ...). Use to scope a search to one "
            "service or chapter of a spec"
        ),
        type="string",
    ),
    AttributeInfo(
        name="defines",
        description=(
            "Exact name of a data type, enumeration, notification, operation or resource "
            "(e.g., 'NotifyThresholdCrossing', 'notifyMOICreation', '/notificationSink'): "
            "returns the section that defines it. Case-sensitive"
        ),
        type="string",
    ),
]

DEFAULT_DOCUMENT_CONTENT_DESCRIPTION = "Processed content of 3GPP specifications"
