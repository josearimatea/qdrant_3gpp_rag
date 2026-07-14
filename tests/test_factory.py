"""Tests for QdrantFactory singleton behavior."""

from unittest.mock import patch

from qdrant_3gpp_rag.factory import QdrantFactory


class TestQdrantFactorySingleton:

    def setup_method(self):
        QdrantFactory.reset()

    def teardown_method(self):
        QdrantFactory.reset()

    @patch("qdrant_3gpp_rag.factory.QdrantClient")
    @patch("qdrant_3gpp_rag.factory.HuggingFaceEmbeddings")
    def test_get_instance_returns_same_object(self, mock_emb, mock_client):
        f1 = QdrantFactory.get_instance(device="cpu")
        f2 = QdrantFactory.get_instance(device="cpu")
        assert f1 is f2
        # HuggingFaceEmbeddings should only be called once
        assert mock_emb.call_count == 1

    @patch("qdrant_3gpp_rag.factory.QdrantClient")
    @patch("qdrant_3gpp_rag.factory.HuggingFaceEmbeddings")
    def test_different_device_gives_different_instance(self, mock_emb, mock_client):
        f1 = QdrantFactory.get_instance(device="cpu")
        f2 = QdrantFactory.get_instance(device="cuda")
        assert f1 is not f2
        assert mock_emb.call_count == 2

    @patch("qdrant_3gpp_rag.factory.QdrantClient")
    @patch("qdrant_3gpp_rag.factory.HuggingFaceEmbeddings")
    def test_different_host_gives_different_instance(self, mock_emb, mock_client):
        f1 = QdrantFactory.get_instance(host="localhost")
        f2 = QdrantFactory.get_instance(host="remote-server")
        assert f1 is not f2

    @patch("qdrant_3gpp_rag.factory.QdrantClient")
    @patch("qdrant_3gpp_rag.factory.HuggingFaceEmbeddings")
    def test_reset_clears_instances(self, mock_emb, mock_client):
        f1 = QdrantFactory.get_instance(device="cpu")
        QdrantFactory.reset()
        f2 = QdrantFactory.get_instance(device="cpu")
        assert f1 is not f2

    @patch("qdrant_3gpp_rag.factory.QdrantVectorStore")
    @patch("qdrant_3gpp_rag.factory.QdrantClient")
    @patch("qdrant_3gpp_rag.factory.HuggingFaceEmbeddings")
    def test_get_vector_store_uses_collection_name(self, mock_emb, mock_client, mock_vs_cls):
        factory = QdrantFactory.get_instance()
        vs = factory.get_vector_store("my_collection")
        mock_vs_cls.assert_called_once_with(
            client=factory.client,
            collection_name="my_collection",
            embedding=factory.embeddings,
            vector_name="text-dense",
            sparse_vector_name="text-sparse",
        )
