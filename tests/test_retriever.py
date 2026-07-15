"""Tests for retriever functions."""

import pytest
from unittest.mock import MagicMock

from langchain_core.documents import Document

from qdrant_3gpp_rag import retrieve, format_document, get_relevant_documents
from qdrant_3gpp_rag._types import RetrievalResult


class TestRetrieve:

    def test_manual_filter_mode(self):
        """retrieve() with explicit filters should not require an LLM."""
        mock_factory = MagicMock()
        mock_vs = MagicMock()
        mock_factory.get_vector_store.return_value = mock_vs
        mock_vs.similarity_search.return_value = [
            Document(page_content="test content", metadata={"release": "Rel-18"})
        ]

        result = retrieve(
            "test query",
            factory=mock_factory,
            collection_name="test_collection",
            filters={"release": "Rel-18"},
        )

        assert isinstance(result, RetrievalResult)
        assert len(result.docs) == 1
        assert result.generated_query == "test query"
        assert "Manual filters" in result.generated_filter
        mock_vs.similarity_search.assert_called_once()

    def test_plain_search_without_filters_or_llm(self):
        """retrieve() with neither filters nor llm does a plain dense search."""
        mock_factory = MagicMock()
        mock_vs = MagicMock()
        mock_factory.get_vector_store.return_value = mock_vs
        mock_vs.similarity_search.return_value = [
            Document(page_content="plain hit", metadata={})
        ]

        result = retrieve(
            "test query",
            factory=mock_factory,
            collection_name="test_collection",
        )

        assert len(result.docs) == 1
        assert result.generated_filter == "No filter applied"
        # No metadata filter passed to the store.
        _, kwargs = mock_vs.similarity_search.call_args
        assert kwargs.get("filter") is None

    def test_self_query_mode_infers_filters(self):
        """With an LLM and no manual filters, the LLM-inferred filters are applied."""
        mock_factory = MagicMock()
        mock_vs = MagicMock()
        mock_factory.get_vector_store.return_value = mock_vs
        mock_vs.similarity_search.return_value = []

        # LLM returns a structured object: rewritten query + one filter set.
        parsed = MagicMock()
        parsed.model_dump.return_value = {
            "query": "createMOI parameters", "release": "Rel-18",
            "series": None, "spec": None,
        }
        structured_llm = MagicMock()
        structured_llm.invoke.return_value = parsed
        mock_llm = MagicMock()
        mock_llm.with_structured_output.return_value = structured_llm

        result = retrieve(
            "createMOI parameters in Rel-18",
            factory=mock_factory,
            collection_name="coll",
            llm=mock_llm,
        )

        assert result.generated_query == "createMOI parameters"
        assert "release" in result.generated_filter
        mock_vs.similarity_search.assert_called_once()

    def test_multiple_filters(self):
        """retrieve() should handle multiple filter keys."""
        mock_factory = MagicMock()
        mock_vs = MagicMock()
        mock_factory.get_vector_store.return_value = mock_vs
        mock_vs.similarity_search.return_value = []

        result = retrieve(
            "query",
            factory=mock_factory,
            collection_name="coll",
            filters={"release": "Rel-18", "spec": "28532"},
        )

        assert len(result.docs) == 0
        assert "Rel-18" in result.generated_filter
        assert "28532" in result.generated_filter


class TestFormatDocument:

    def test_format_with_full_metadata(self):
        doc = Document(
            page_content="Some content here",
            metadata={
                "release": "Rel-18",
                "series": "28_series",
                "spec": "28532",
                "chunk_index": 5,
            },
        )
        result = format_document(doc)
        assert "release:     Rel-18" in result
        assert "series:      28_series" in result
        assert "spec:        28532" in result
        assert "chunk_index: 5" in result
        assert "Some content here" in result

    def test_format_with_missing_metadata(self):
        doc = Document(page_content="content only", metadata={})
        result = format_document(doc)
        assert "release:     unknown" in result
        assert "content only" in result


class TestGetRelevantDocuments:

    def test_returns_formatted_strings(self):
        mock_factory = MagicMock()
        mock_vs = MagicMock()
        mock_factory.get_vector_store.return_value = mock_vs
        mock_vs.similarity_search.return_value = [
            Document(page_content="doc1", metadata={"release": "Rel-18"}),
            Document(page_content="doc2", metadata={"release": "Rel-17"}),
        ]

        strings = get_relevant_documents(
            "query",
            factory=mock_factory,
            collection_name="coll",
            filters={"release": "Rel-18"},
        )

        assert len(strings) == 2
        assert isinstance(strings[0], str)
        assert "doc1" in strings[0]
        assert "doc2" in strings[1]
