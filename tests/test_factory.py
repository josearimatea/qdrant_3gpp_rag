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

    @staticmethod
    def _collection(factory, sparse):
        factory.client.collection_exists.return_value = True
        factory.client.get_collection.return_value.config.params.sparse_vectors = (
            {"text-sparse": object()} if sparse else None)

    @patch("qdrant_3gpp_rag.factory.QdrantVectorStore")
    @patch("qdrant_3gpp_rag.factory.QdrantClient")
    @patch("qdrant_3gpp_rag.factory.HuggingFaceEmbeddings")
    def test_a_dense_only_collection_is_searched_dense(self, mock_emb, mock_client, mock_vs_cls):
        factory = QdrantFactory.get_instance()
        self._collection(factory, sparse=False)
        factory.get_vector_store("my_collection")
        mock_vs_cls.assert_called_once_with(
            client=factory.client,
            collection_name="my_collection",
            embedding=factory.embeddings,
            vector_name="text-dense",
        )

    @patch("qdrant_3gpp_rag.factory.FastEmbedSparse")
    @patch("qdrant_3gpp_rag.factory.QdrantVectorStore")
    @patch("qdrant_3gpp_rag.factory.QdrantClient")
    @patch("qdrant_3gpp_rag.factory.HuggingFaceEmbeddings")
    def test_a_collection_with_the_sparse_vector_is_searched_hybrid(
            self, mock_emb, mock_client, mock_vs_cls, mock_sparse):
        from langchain_qdrant import RetrievalMode

        factory = QdrantFactory.get_instance()
        self._collection(factory, sparse=True)
        factory.get_vector_store("my_collection")
        kwargs = mock_vs_cls.call_args.kwargs
        assert kwargs["sparse_vector_name"] == "text-sparse"
        assert kwargs["retrieval_mode"] == RetrievalMode.HYBRID
        mock_sparse.assert_called_once_with(model_name="Qdrant/bm25")

    @patch("qdrant_3gpp_rag.factory.FastEmbedSparse")
    @patch("qdrant_3gpp_rag.factory.QdrantVectorStore")
    @patch("qdrant_3gpp_rag.factory.QdrantClient")
    @patch("qdrant_3gpp_rag.factory.HuggingFaceEmbeddings")
    def test_none_forces_dense_and_never_loads_bm25(
            self, mock_emb, mock_client, mock_vs_cls, mock_sparse):
        factory = QdrantFactory.get_instance()
        self._collection(factory, sparse=True)
        factory.get_vector_store("my_collection", sparse_vector_name=None)
        assert "sparse_vector_name" not in mock_vs_cls.call_args.kwargs
        mock_sparse.assert_not_called()


class TestBuildEmbeddings:

    @patch("qdrant_3gpp_rag.factory.HuggingFaceEmbeddings")
    def test_the_default_model_gets_its_query_prefix_on_queries_only(self, mock_emb):
        from qdrant_3gpp_rag.factory import DEFAULT_EMBEDDING_MODEL, build_embeddings

        build_embeddings(device="cpu")
        kwargs = mock_emb.call_args.kwargs
        assert kwargs["model_name"] == DEFAULT_EMBEDDING_MODEL == "BAAI/bge-base-en-v1.5"
        assert kwargs["encode_kwargs"] == {"normalize_embeddings": True}       # passages
        assert kwargs["query_encode_kwargs"] == {                                 # queries
            "normalize_embeddings": True,
            "prompt": "Represent this sentence for searching relevant passages: ",
        }

    @patch("qdrant_3gpp_rag.factory.HuggingFaceEmbeddings")
    def test_a_model_without_a_known_prefix_gets_none(self, mock_emb):
        from qdrant_3gpp_rag.factory import build_embeddings

        build_embeddings(model_name="some/other-model", device="cpu")
        assert mock_emb.call_args.kwargs["query_encode_kwargs"] == {}

    @patch("torch.cuda.is_available")
    def test_the_default_device_is_the_gpu_when_there_is_one(self, mock_cuda):
        from qdrant_3gpp_rag.factory import default_device

        mock_cuda.return_value = True
        assert default_device() == "cuda"
        mock_cuda.return_value = False
        assert default_device() == "cpu"
