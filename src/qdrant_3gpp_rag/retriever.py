"""
Unified retriever for Qdrant-backed RAG.

Two retrieval strategies:
  - Manual filter mode: caller provides explicit metadata filters.
  - Self-query mode: an LLM infers the metadata filters from the natural-language
    query (via structured output), then a similarity search runs with those
    filters applied — hybrid (dense + BM25) when the collection has a sparse
    vector, dense-only otherwise.

The self-query path is built on langchain_core structured output — the LLM fills
a small Pydantic model of filter fields — rather than the legacy
langchain.retrievers.self_query module (removed in LangChain 1.x). No dependency
on langchain_classic.

Convenience wrappers get_relevant_documents() and get_relevant_chunks() return
formatted strings ready for prompt injection.
"""

import logging
from typing import Any, Callable, Dict, List, Optional, Sequence, Type

from langchain_core.documents import Document
from langchain_core.language_models import BaseLanguageModel
from langchain_qdrant import RetrievalMode
from pydantic import BaseModel, Field, create_model
from qdrant_client.http.models import FieldCondition, Filter, MatchValue

from qdrant_3gpp_rag._types import RetrievalResult
from qdrant_3gpp_rag.factory import AUTO, QdrantFactory
from qdrant_3gpp_rag.self_query import (
    DEFAULT_DOCUMENT_CONTENT_DESCRIPTION,
    DEFAULT_METADATA_FIELD_INFO,
)

logger = logging.getLogger(__name__)

# A hybrid search asks for this many times k, to order the results itself: see _search.
HYBRID_CANDIDATES_PER_RESULT = 4


def _build_filter_model(
    metadata_field_info: Sequence,
    document_content_description: str,
) -> Type[BaseModel]:
    """Create a Pydantic model the LLM fills to describe the query's filters.

    One optional string field per metadata attribute (from metadata_field_info),
    each documented with the attribute's description, plus a rewritten `query`
    for the semantic search. All filter fields are optional — the LLM leaves a
    field None when the query does not constrain it.
    """
    fields: Dict[str, Any] = {
        "query": (
            str,
            Field(description=(
                "The semantic search string, with any metadata constraints removed. "
                f"Content indexed: {document_content_description}."
            )),
        ),
    }
    for info in metadata_field_info:
        name = getattr(info, "name", None) or info["name"]
        desc = getattr(info, "description", None) or info.get("description", "")
        fields[name] = (
            Optional[str],
            Field(default=None, description=f"Filter value for '{name}': {desc}. Null if not constrained."),
        )
    return create_model("SelfQueryFilters", **fields)


# langchain_qdrant nests document metadata under a "metadata" payload key, so a
# field indexed as {"mns": ...} lives at "metadata.mns" in Qdrant. Filter keys are
# prefixed accordingly (unless the caller already qualified them).
_METADATA_PAYLOAD_KEY = "metadata"


def _payload_key(key: str) -> str:
    return key if key.startswith(_METADATA_PAYLOAD_KEY + ".") else f"{_METADATA_PAYLOAD_KEY}.{key}"


def _conditions(group: Dict[str, Any]) -> List[FieldCondition]:
    """Exact-match conditions for a flat {key: value} dict, empty values ignored."""
    return [
        FieldCondition(key=_payload_key(key), match=MatchValue(value=value))
        for key, value in group.items()
        if value not in (None, "")
    ]


def _to_qdrant_filter(
    filters: Dict[str, Any],
    exclude: Optional[List[Dict[str, Any]]] = None,
) -> Optional[Filter]:
    """Build a Qdrant Filter from a flat {key: value} dict, ignoring empty values.

    Keys are resolved against the nested metadata payload (e.g. "mns" ->
    "metadata.mns"), matching how langchain_qdrant stores document metadata.

    exclude: groups of conditions, each in the same format as `filters`; a chunk
    matching ALL the conditions of ANY group is left out (one nested must_not per
    group). {"spec_number": "28532", "annex": True} drops that spec's annexes and
    keeps every other spec's. A group left empty by its empty values is ignored.
    """
    must = _conditions(filters or {})
    must_not = [Filter(must=conds) for conds in map(_conditions, exclude or []) if conds]
    if not must and not must_not:
        return None
    return Filter(must=must or None, must_not=must_not or None)


def build_self_query_filters(
    query: str,
    *,
    llm: BaseLanguageModel,
    metadata_field_info: Optional[Sequence] = None,
    document_content_description: Optional[str] = None,
) -> tuple[str, Dict[str, Any]]:
    """Ask the LLM to split a query into a semantic string + metadata filters.

    Returns (search_query, filters) where filters holds only the constrained
    fields. Modern replacement for SelfQueryRetriever's query constructor.
    """
    fields = metadata_field_info or DEFAULT_METADATA_FIELD_INFO
    content_desc = document_content_description or DEFAULT_DOCUMENT_CONTENT_DESCRIPTION
    model = _build_filter_model(fields, content_desc)

    parsed = llm.with_structured_output(model).invoke(query)
    data = parsed.model_dump()
    search_query = data.pop("query", None) or query
    filters = {k: v for k, v in data.items() if v not in (None, "")}
    return search_query, filters


def format_document(doc: Document) -> str:
    """Format a Document as string with metadata header + content."""
    md = doc.metadata or {}
    header = (
        f"release:     {md.get('release', 'unknown')}\n"
        f"series:      {md.get('series', 'unknown')}\n"
        f"spec:        {md.get('spec', 'unknown')}\n"
        f"chunk_index: {md.get('chunk_index', 'unknown')}\n\n"
    )
    return header + doc.page_content.strip()


def _search(vector_store: Any, query: str, k: int, qdrant_filter: Optional[Filter]) -> List[Document]:
    """The k best documents, the same ones in the same order on every call.

    RRF scores a chunk only by its positions in the dense and BM25 lists, so exact ties
    are common (1st and 3rd ties with 3rd and 1st), and Qdrant orders tied points
    differently from call to call. A hybrid search thus fetches more than k, orders by
    (score, point id) and only then cuts, so a tie at the cut cannot change the result.
    A dense search returns a stable order as it is.
    """
    if getattr(vector_store, "retrieval_mode", None) != RetrievalMode.HYBRID:
        return vector_store.similarity_search(query=query, k=k, filter=qdrant_filter)
    scored = vector_store.similarity_search_with_score(
        query=query, k=k * HYBRID_CANDIDATES_PER_RESULT, filter=qdrant_filter)
    scored.sort(key=lambda pair: (-pair[1], str(pair[0].metadata["_id"])))
    return [doc for doc, _ in scored[:k]]


def retrieve(
    query: str,
    *,
    factory: QdrantFactory,
    collection_name: str,
    llm: Optional[BaseLanguageModel] = None,
    k: int = 5,
    filters: Optional[Dict[str, Any]] = None,
    metadata_field_info: Optional[Sequence] = None,
    document_content_description: Optional[str] = None,
    sparse_vector_name: Optional[str] = AUTO,
    exclude: Optional[List[Dict[str, Any]]] = None,
) -> RetrievalResult:
    """Core retrieval function.

    Three modes, chosen by the arguments provided:
      - filters given: manual exact filter + similarity search.
      - no filters, llm given: the LLM infers filters from the query (self-query),
        then the same filtered similarity search runs.
      - no filters, no llm: plain similarity search over the whole
        collection (no metadata filter).

    Args:
        query: Natural language query.
        factory: QdrantFactory instance (use get_instance() for the singleton).
        collection_name: Qdrant collection name.
        llm: Required only for self-query mode (no manual filters).
        k: Number of results.
        filters: Dict of metadata key-value pairs for exact matching.
        metadata_field_info: Override default metadata schema for self-query.
        document_content_description: Override default for self-query.
        sparse_vector_name: AUTO (default) searches hybrid — dense + BM25, fused
            by RRF — when the collection holds the sparse vector, dense-only
            otherwise; None forces dense-only.
        exclude: groups of metadata conditions to leave out, e.g.
            [{"spec_number": "28532", "annex": True}]: a chunk matching every
            condition of a group is excluded. Applied in all three modes (with
            self-query it is added to the inferred filters, never inferred nor
            dropped) and to both legs of a hybrid search.

    Returns:
        RetrievalResult with docs, generated_query, and generated_filter.
    """
    logger.info("Starting retrieval | query=%r | k=%d | filters=%s | exclude=%s",
                query, k, filters, exclude)

    vector_store = factory.get_vector_store(
        collection_name, sparse_vector_name=sparse_vector_name
    )

    if filters:
        logger.info("Using manual filter mode")
        search_query = query
        applied_filters = filters
        filter_desc = f"Manual filters: {filters}"
    elif llm is not None:
        logger.info("Using self-query mode (LLM-inferred filters)")
        search_query, applied_filters = build_self_query_filters(
            query,
            llm=llm,
            metadata_field_info=metadata_field_info,
            document_content_description=document_content_description,
        )
        filter_desc = str(applied_filters) if applied_filters else "No filter applied"
    else:
        # Plain similarity search — no metadata filter, no LLM.
        logger.info("Using plain vector search (no filters, no LLM)")
        search_query = query
        applied_filters = {}
        filter_desc = "No filter applied"

    qdrant_filter = _to_qdrant_filter(applied_filters, exclude)
    if qdrant_filter is not None and qdrant_filter.must_not:
        filter_desc = (f"{'' if filter_desc == 'No filter applied' else filter_desc + ' | '}"
                       f"Excluded: {exclude}")
    docs = _search(vector_store, search_query, k, qdrant_filter)

    result = RetrievalResult(
        docs=docs,
        generated_query=search_query,
        generated_filter=filter_desc,
    )
    logger.info("Retrieval finished -> %d documents found", len(result.docs))
    logger.debug("Generated query:  %s", result.generated_query)
    logger.debug("Generated filter: %s", result.generated_filter)
    return result


def get_relevant_documents(
    query: str,
    *,
    factory: QdrantFactory,
    collection_name: str,
    llm: Optional[BaseLanguageModel] = None,
    k: int = 5,
    filters: Optional[Dict[str, Any]] = None,
    formatter: Callable[[Document], str] = format_document,
    sparse_vector_name: Optional[str] = AUTO,
    exclude: Optional[List[Dict[str, Any]]] = None,
) -> List[str]:
    """Retrieve + format as strings ready for prompt injection."""
    result = retrieve(
        query, factory=factory, collection_name=collection_name,
        llm=llm, k=k, filters=filters, sparse_vector_name=sparse_vector_name,
        exclude=exclude,
    )
    formatted = [formatter(doc) for doc in result.docs]
    logger.info("get_relevant_documents -> formatted %d strings", len(formatted))
    return formatted


# Alias for rulesbank compatibility
get_relevant_chunks = get_relevant_documents
