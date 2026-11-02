"""retrieve(exclude=...) against the local Qdrant (3gpp_rel18_all); skipped without it.

No LLM: manual filters and plain search only.
"""

import socket

import pytest

COLLECTION = "3gpp_rel18_all"
EXCLUDE = [{"spec_number": "28532", "annex": True}]
QUERY = "NotificationHeader notificationId eventTime systemDN"


def _qdrant_has_collection() -> bool:
    try:
        with socket.create_connection(("localhost", 6333), timeout=2):
            pass
        from qdrant_client import QdrantClient

        return QdrantClient("localhost", port=6333, timeout=10).collection_exists(COLLECTION)
    except Exception:
        return False


pytestmark = pytest.mark.skipif(not _qdrant_has_collection(),
                                reason=f"local Qdrant with '{COLLECTION}' not available")


@pytest.fixture(scope="module")
def factory():
    from qdrant_3gpp_rag import QdrantFactory

    return QdrantFactory.get_instance()


def _is_excluded(doc) -> bool:
    return doc.metadata.get("spec_number") == "28532" and doc.metadata.get("annex") is True


def test_the_excluded_group_has_no_point_left(factory):
    from qdrant_3gpp_rag.retriever import _to_qdrant_filter

    target = _to_qdrant_filter({"spec_number": "28532", "annex": True})
    assert factory.client.count(COLLECTION, count_filter=target).count > 0     # it exists...
    both = _to_qdrant_filter({"spec_number": "28532", "annex": True}, exclude=EXCLUDE)
    assert factory.client.count(COLLECTION, count_filter=both).count == 0      # ...and is cut
    other = _to_qdrant_filter({"spec_number": "28623", "annex": True}, exclude=EXCLUDE)
    assert factory.client.count(COLLECTION, count_filter=other).count > 0      # others stay


def test_a_search_with_exclude_skips_the_spec_annex_but_keeps_other_annexes(factory):
    from qdrant_3gpp_rag import retrieve

    docs = retrieve(QUERY, factory=factory, collection_name=COLLECTION, k=20,
                    exclude=EXCLUDE).docs
    assert docs and not any(_is_excluded(d) for d in docs)
    assert any(d.metadata.get("spec_number") == "28623" and d.metadata.get("annex") for d in docs)

    in_spec = retrieve("PerfMnS OpenAPI notifyThresholdCrossing NotifyThresholdCrossing yaml",
                       factory=factory, collection_name=COLLECTION, k=20,
                       filters={"spec_number": "28532"}, exclude=EXCLUDE).docs
    assert in_spec and not any(d.metadata.get("annex") for d in in_spec)


def test_without_exclude_the_results_are_unchanged(factory):
    """Compared dense-only: the hybrid RRF fusion breaks exact score ties in an
    arbitrary order, so two identical hybrid searches can differ in order (and at the
    k cut); dense and BM25 searches on their own are deterministic."""
    from qdrant_3gpp_rag import retrieve

    via_retrieve = retrieve(QUERY, factory=factory, collection_name=COLLECTION, k=10,
                            sparse_vector_name=None).docs
    direct = factory.get_vector_store(COLLECTION, sparse_vector_name=None).similarity_search(
        QUERY, k=10)
    assert [d.metadata["content_hash"] for d in via_retrieve] == \
           [d.metadata["content_hash"] for d in direct]


@pytest.mark.parametrize("filters, exclude", [
    (None, None),
    ({"spec_number": "28532"}, None),
    (None, EXCLUDE),
])
def test_a_repeated_hybrid_search_returns_the_same_list(factory, filters, exclude):
    from qdrant_3gpp_rag import retrieve

    runs = [[d.metadata["content_hash"] for d in
             retrieve(QUERY, factory=factory, collection_name=COLLECTION, k=5,
                      filters=filters, exclude=exclude).docs] for _ in range(6)]
    assert runs[0] and all(run == runs[0] for run in runs)
