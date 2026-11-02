"""Tests for retriever functions."""

import pytest
from unittest.mock import MagicMock

from langchain_core.documents import Document

from qdrant_3gpp_rag import retrieve, format_document, get_relevant_documents
from qdrant_3gpp_rag._types import RetrievalResult
from qdrant_3gpp_rag.retriever import _to_qdrant_filter


class TestToQdrantFilter:

    def test_keys_are_prefixed_with_metadata(self):
        """langchain_qdrant nests metadata, so filter keys need a 'metadata.' prefix."""
        qf = _to_qdrant_filter({"mns": "ProvMnS", "stage": "2"})
        keys = {c.key for c in qf.must}
        assert keys == {"metadata.mns", "metadata.stage"}

    def test_already_qualified_key_not_double_prefixed(self):
        qf = _to_qdrant_filter({"metadata.spec": "28532"})
        assert [c.key for c in qf.must] == ["metadata.spec"]

    def test_empty_values_dropped(self):
        qf = _to_qdrant_filter({"mns": "ProvMnS", "stage": None, "series": ""})
        assert [c.key for c in qf.must] == ["metadata.mns"]

    def test_all_empty_returns_none(self):
        assert _to_qdrant_filter({"a": None, "b": ""}) is None

    def test_only_filters_has_no_must_not(self):
        qf = _to_qdrant_filter({"spec": "28532-i00"})
        assert [c.key for c in qf.must] == ["metadata.spec"] and qf.must_not is None

    def test_only_exclude(self):
        qf = _to_qdrant_filter({}, exclude=[{"spec_number": "28532", "annex": True}])
        assert qf.must is None and len(qf.must_not) == 1
        group = qf.must_not[0]
        assert {(c.key, c.match.value) for c in group.must} == {
            ("metadata.spec_number", "28532"), ("metadata.annex", True)}

    def test_filters_and_exclude_together(self):
        qf = _to_qdrant_filter({"defines": "NotificationHeader"},
                               exclude=[{"spec_number": "28532", "annex": True},
                                        {"spec": "28623-i40"}])
        assert [c.key for c in qf.must] == ["metadata.defines"]
        assert [len(g.must) for g in qf.must_not] == [2, 1]      # one nested filter per group

    def test_empty_values_and_empty_groups_in_exclude_are_ignored(self):
        qf = _to_qdrant_filter({}, exclude=[{"spec_number": "28532", "annex": None},
                                            {"spec": "", "clause": None}])
        assert len(qf.must_not) == 1
        assert [c.key for c in qf.must_not[0].must] == ["metadata.spec_number"]
        assert _to_qdrant_filter({}, exclude=[{"spec": ""}]) is None

    def test_false_is_a_value_not_an_empty_one(self):
        qf = _to_qdrant_filter({"annex": False})
        assert qf.must[0].match.value is False


def _store(docs=()):
    factory, vs = MagicMock(), MagicMock()
    factory.get_vector_store.return_value = vs
    vs.similarity_search.return_value = list(docs)
    return factory, vs


EXCLUDE = [{"spec_number": "28532", "annex": True}]


class TestRetrieveWithExclude:

    def test_manual_filters_and_exclude_go_in_the_same_filter(self):
        factory, vs = _store()
        result = retrieve("q", factory=factory, collection_name="c",
                          filters={"defines": "NotificationHeader"}, exclude=EXCLUDE)
        qf = vs.similarity_search.call_args.kwargs["filter"]
        assert [c.key for c in qf.must] == ["metadata.defines"] and len(qf.must_not) == 1
        assert "Manual filters" in result.generated_filter and "Excluded" in result.generated_filter

    def test_plain_search_gets_only_the_must_not(self):
        factory, vs = _store()
        result = retrieve("q", factory=factory, collection_name="c", exclude=EXCLUDE)
        qf = vs.similarity_search.call_args.kwargs["filter"]
        assert qf.must is None and len(qf.must_not) == 1
        assert result.generated_filter.startswith("Excluded")

    def test_self_query_adds_inferred_filters_to_the_exclusion(self):
        factory, vs = _store()
        parsed = MagicMock()
        parsed.model_dump.return_value = {"query": "header fields", "spec_number": "28623"}
        llm = MagicMock()
        llm.with_structured_output.return_value.invoke.return_value = parsed
        retrieve("header fields in 28623", factory=factory, collection_name="c", llm=llm,
                 exclude=EXCLUDE)
        qf = vs.similarity_search.call_args.kwargs["filter"]
        assert [c.key for c in qf.must] == ["metadata.spec_number"] and len(qf.must_not) == 1

    def test_without_exclude_nothing_changes(self):
        factory, vs = _store()
        result = retrieve("q", factory=factory, collection_name="c")
        assert vs.similarity_search.call_args.kwargs["filter"] is None
        assert result.generated_filter == "No filter applied"

    def test_get_relevant_documents_passes_exclude_through(self):
        factory, vs = _store([Document(page_content="x", metadata={})])
        get_relevant_documents("q", factory=factory, collection_name="c", exclude=EXCLUDE)
        assert len(vs.similarity_search.call_args.kwargs["filter"].must_not) == 1


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


class TestHybridSearchIsReproducible:

    @staticmethod
    def _store(pairs):
        from langchain_qdrant import RetrievalMode

        vs = MagicMock()
        vs.retrieval_mode = RetrievalMode.HYBRID
        vs.similarity_search_with_score.side_effect = lambda **kw: list(pairs)
        factory = MagicMock()
        factory.get_vector_store.return_value = vs
        return factory, vs

    @staticmethod
    def _pair(pid, score):
        return Document(page_content=pid, metadata={"_id": pid}), score

    def test_ties_are_ordered_by_point_id_whatever_order_qdrant_returns(self):
        pairs = [("b", 0.5), ("c", 0.3), ("a", 0.5), ("d", 0.6)]
        orders = [pairs, list(reversed(pairs))]
        got = []
        for order in orders:
            factory, _ = self._store([self._pair(*p) for p in order])
            docs = retrieve("q", factory=factory, collection_name="c", k=4).docs
            got.append([d.metadata["_id"] for d in docs])
        assert got == [["d", "a", "b", "c"]] * 2

    def test_a_tie_at_the_cut_keeps_the_same_document(self):
        factory, _ = self._store([self._pair("z", 0.5), self._pair("a", 0.5), self._pair("y", 0.9)])
        docs = retrieve("q", factory=factory, collection_name="c", k=2).docs
        assert [d.metadata["_id"] for d in docs] == ["y", "a"]

    def test_it_fetches_more_than_k_and_keeps_the_filter(self):
        factory, vs = self._store([self._pair("a", 0.5)])
        retrieve("q", factory=factory, collection_name="c", k=5, filters={"spec": "x"})
        kwargs = vs.similarity_search_with_score.call_args.kwargs
        assert kwargs["k"] == 20 and kwargs["filter"].must[0].key == "metadata.spec"
        vs.similarity_search.assert_not_called()

    def test_a_dense_search_is_left_as_it_is(self):
        from langchain_qdrant import RetrievalMode

        factory, vs = MagicMock(), MagicMock()
        vs.retrieval_mode = RetrievalMode.DENSE
        vs.similarity_search.return_value = [Document(page_content="a", metadata={})]
        factory.get_vector_store.return_value = vs
        assert len(retrieve("q", factory=factory, collection_name="c", k=5).docs) == 1
        assert vs.similarity_search.call_args.kwargs["k"] == 5
        vs.similarity_search_with_score.assert_not_called()
