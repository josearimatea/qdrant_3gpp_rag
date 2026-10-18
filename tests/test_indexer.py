"""Tests for the generic document indexer."""

from unittest.mock import MagicMock

import pytest

from qdrant_3gpp_rag.indexer import (
    chunk_markdown_by_headers,
    chunk_text,
    drop_collection,
    index_document,
    index_segments,
)


class TestChunkText:

    def test_splits_long_text(self):
        text = "word " * 2000  # ~10k chars
        chunks = chunk_text(text, chunk_size=1000, chunk_overlap=200)
        assert len(chunks) > 1
        assert all(isinstance(c, str) for c in chunks)

    def test_short_text_single_chunk(self):
        chunks = chunk_text("a short document", chunk_size=1000, chunk_overlap=200)
        assert chunks == ["a short document"]


class TestChunkMarkdownByHeaders:

    def test_splits_on_headers(self):
        md = "# A\nalpha content\n\n## B\nbeta content\n\n### C\ngamma content"
        chunks = chunk_markdown_by_headers(md, chunk_size=1000, chunk_overlap=0)
        # Each section becomes its own chunk (small enough not to be size-split).
        joined = "\n".join(chunks)
        assert "alpha content" in joined and "beta content" in joined and "gamma content" in joined
        # A section's content is not merged across a header boundary into one chunk
        # with another section's — at least three chunks for three headers.
        assert len(chunks) >= 3

    def test_large_section_further_split_by_size(self):
        md = "# Big\n" + ("word " * 500)  # one header, oversized body
        chunks = chunk_markdown_by_headers(md, chunk_size=200, chunk_overlap=20)
        assert len(chunks) > 1

    def test_plain_text_without_headers(self):
        chunks = chunk_markdown_by_headers("no headers here, just text", chunk_size=1000)
        assert len(chunks) == 1
        assert "no headers here" in chunks[0]


class TestIndexDocument:

    def _factory(self, exists=False):
        factory = MagicMock()
        factory.client.collection_exists.return_value = exists
        factory.client.count.return_value = MagicMock(count=3)
        return factory

    def test_empty_text_raises(self):
        factory = self._factory()
        with pytest.raises(ValueError, match="empty document"):
            index_document("   ", factory=factory, collection_name="c")

    def test_creates_collection_and_indexes(self):
        factory = self._factory(exists=False)
        vs = MagicMock()
        factory.get_vector_store.return_value = vs

        n = index_document(
            "some content to index that is long enough",
            factory=factory,
            collection_name="spec_tmp",
            metadata={"release": "Rel-18", "series": "28_series", "spec": "28532"},
        )

        # Collection created, dense-only vector store requested, chunks added.
        factory.client.create_collection.assert_called_once()
        factory.get_vector_store.assert_called_once_with("spec_tmp", sparse_vector_name=None)
        vs.add_texts.assert_called_once()
        assert n >= 1

    def test_metadata_carries_chunk_index(self):
        factory = self._factory(exists=False)
        vs = MagicMock()
        factory.get_vector_store.return_value = vs

        index_document(
            "content",
            factory=factory,
            collection_name="c",
            metadata={"spec": "28532"},
        )

        _, kwargs = vs.add_texts.call_args
        metadatas = kwargs["metadatas"]
        assert all(m["spec"] == "28532" for m in metadatas)
        assert [m["chunk_index"] for m in metadatas] == list(range(len(metadatas)))

    def test_recreate_drops_existing(self):
        factory = self._factory(exists=True)
        factory.get_vector_store.return_value = MagicMock()

        index_document("content", factory=factory, collection_name="c", recreate=True)

        factory.client.delete_collection.assert_called_once_with("c")


class TestIndexSegments:

    def test_new_collection_gets_keyword_indexes_on_the_metadata(self):
        factory = MagicMock()
        factory.client.collection_exists.return_value = False
        factory.client.count.return_value = MagicMock(count=1)
        vs = MagicMock()
        factory.get_vector_store.return_value = vs

        index_segments(
            [{"text": "a section", "spec": "28532-i00", "clause": "12.3",
              "clause_ancestors": ["12", "12.3"], "defines": [], "mentions": []}],
            factory=factory, collection_name="c", chunk_size=None,
        )

        sparse = factory.client.create_collection.call_args.kwargs["sparse_vectors_config"]
        assert list(sparse) == ["text-sparse"] and sparse["text-sparse"].modifier == "idf"
        factory.get_vector_store.assert_called_once_with("c", sparse_vector_name="auto")
        indexed = {c.kwargs["field_name"] for c in factory.client.create_payload_index.call_args_list}
        assert indexed == {"metadata.spec", "metadata.clause", "metadata.clause_ancestors",
                           "metadata.defines", "metadata.mentions"}
        _, kwargs = vs.add_texts.call_args
        assert kwargs["metadatas"][0]["clause_ancestors"] == ["12", "12.3"]


    def test_hybrid_false_creates_a_dense_only_collection(self):
        factory = MagicMock()
        factory.client.collection_exists.return_value = False
        factory.client.count.return_value = MagicMock(count=1)
        index_segments([{"text": "x"}], factory=factory, collection_name="c",
                       chunk_size=None, hybrid=False)
        assert factory.client.create_collection.call_args.kwargs["sparse_vectors_config"] is None


class TestDropCollection:

    def test_drops_when_exists(self):
        factory = MagicMock()
        factory.client.collection_exists.return_value = True
        assert drop_collection(factory, "c") is True
        factory.client.delete_collection.assert_called_once_with("c")

    def test_noop_when_missing(self):
        factory = MagicMock()
        factory.client.collection_exists.return_value = False
        assert drop_collection(factory, "c") is False
        factory.client.delete_collection.assert_not_called()
