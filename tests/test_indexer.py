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
        assert indexed == {"metadata.spec", "metadata.spec_number", "metadata.clause",
                           "metadata.clause_ancestors", "metadata.defines",
                           "metadata.mentions", "metadata.annex"}
        _, kwargs = vs.add_texts.call_args
        assert kwargs["metadatas"][0]["clause_ancestors"] == ["12", "12.3"]
        assert kwargs["batch_size"] == 512

    def test_a_new_collection_is_bulk_loaded_and_indexed_once_at_the_end(self):
        factory = MagicMock()
        factory.client.collection_exists.return_value = False
        factory.client.count.return_value = MagicMock(count=1)
        factory.get_vector_store.return_value.add_texts.side_effect = RuntimeError("boom")

        with pytest.raises(RuntimeError):
            index_segments([{"text": "x"}], factory=factory, collection_name="c", chunk_size=None)

        created = factory.client.create_collection.call_args.kwargs["optimizers_config"]
        assert created.indexing_threshold == 0                      # no HNSW while loading
        restored = factory.client.update_collection.call_args.kwargs["optimizers_config"]
        assert restored.indexing_threshold == 10000                 # back on, even after a failure


    def test_hybrid_false_creates_a_dense_only_collection(self):
        factory = MagicMock()
        factory.client.collection_exists.return_value = False
        factory.client.count.return_value = MagicMock(count=1)
        index_segments([{"text": "x"}], factory=factory, collection_name="c",
                       chunk_size=None, hybrid=False)
        assert factory.client.create_collection.call_args.kwargs["sparse_vectors_config"] is None


class TestIncrementalIndexing:
    """Point ids come from the chunk's content: the collection tells what is indexed."""

    SEGS = [{"text": "section one", "spec": "28532-i00"},
            {"text": "section two", "spec": "28532-i00"}]

    def _factory(self, existing_ids=(), exists=True):
        from types import SimpleNamespace

        factory = MagicMock()
        factory.client.collection_exists.return_value = exists
        factory.client.scroll.return_value = (
            [SimpleNamespace(id=i) for i in existing_ids], None)
        factory.client.count.return_value = MagicMock(count=len(existing_ids))
        return factory

    def _ids(self):
        f = self._factory(exists=False)
        index_segments(self.SEGS, factory=f, collection_name="c", chunk_size=None)
        kwargs = f.get_vector_store.return_value.add_texts.call_args.kwargs
        return kwargs["ids"], kwargs["metadatas"]

    def test_the_same_chunk_always_gets_the_same_id_and_a_content_hash(self):
        ids1, metas = self._ids()
        ids2, _ = self._ids()
        assert ids1 == ids2 and len(set(ids1)) == 2
        assert all(len(m["content_hash"]) == 64 for m in metas)

    def test_a_sync_writes_only_the_missing_chunks(self):
        ids, _ = self._ids()
        f = self._factory(existing_ids=[ids[0]])
        n = index_segments(self.SEGS, factory=f, collection_name="c", chunk_size=None,
                           recreate=False)
        assert n == 1
        assert f.get_vector_store.return_value.add_texts.call_args.kwargs["ids"] == [ids[1]]
        f.client.delete_collection.assert_not_called()
        f.client.delete.assert_not_called()                 # stale points kept by default

    def test_a_rerun_writes_nothing_and_prune_deletes_what_is_gone(self):
        ids, _ = self._ids()
        old = "00000000-0000-0000-0000-000000000001"
        f = self._factory(existing_ids=[*ids, old])
        n = index_segments(self.SEGS, factory=f, collection_name="c", chunk_size=None,
                           recreate=False, prune=True)
        assert n == 0
        f.get_vector_store.return_value.add_texts.assert_not_called()
        assert f.client.delete.call_args.kwargs["points_selector"].points == [old]

    def test_dry_run_changes_nothing(self):
        f = self._factory(existing_ids=[])
        n = index_segments(self.SEGS, factory=f, collection_name="c", chunk_size=None,
                           recreate=False, dry_run=True)
        assert n == 2
        f.client.create_collection.assert_not_called()
        f.get_vector_store.assert_not_called()
        f.client.delete.assert_not_called()


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
