"""Tests for the generic document indexer."""

from unittest.mock import MagicMock

import pytest

from qdrant_3gpp_rag.indexer import chunk_text, drop_collection, index_document


class TestChunkText:

    def test_splits_long_text(self):
        text = "word " * 2000  # ~10k chars
        chunks = chunk_text(text, chunk_size=1000, chunk_overlap=200)
        assert len(chunks) > 1
        assert all(isinstance(c, str) for c in chunks)

    def test_short_text_single_chunk(self):
        chunks = chunk_text("a short document", chunk_size=1000, chunk_overlap=200)
        assert chunks == ["a short document"]


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
