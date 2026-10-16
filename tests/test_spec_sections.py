"""Chunking a 3GPP spec by section (spec_sections) — one chunk per clause."""

import os

import pytest

from qdrant_3gpp_rag.spec_sections import chunk_spec_sections, clause_of, split_sections

SPEC = """\
Cover page text.

Contents
========

1 Scope 7
12.3 Performance 10

# 1 Scope

The present document specifies management services.

#### 12.3.1.2.3 Resources

##### 12.3.1.2.3.2.1 Resource \\"/notificationSink\\" {#res .H6}

This resource represents a resource on a MnS consumer.

##### 12.3.1.2.3.2.1.3 HTTP methods

###### 12.3.1.2.3.2.1.3.1 POST {#post-4 .H6}

Table 12.3.1.2.3.2.1.3.1-3: Data structures supported by the POST Response Body

+----------------+----------+-------------------------------+---+
| n/a            | 204      | In case of success            | M |
+----------------+----------+-------------------------------+---+
| Error-Response | 4xx/5xx  | In case of failure            | M |
+----------------+----------+-------------------------------+---+

###### 12.3.1.2.4 Data type definitions

Types used by the service.

######## Annex A (normative): OpenAPI specification

A.1 Provisioning management service
===================================

openapi: 3.0.1
"""


def _by_clause(segs):
    return {s["clause"]: s for s in segs}


def test_one_chunk_per_clause_never_two_sections():
    segs = chunk_spec_sections(SPEC, {"spec": "28532-i00"})
    post = _by_clause(segs)["12.3.1.2.3.2.1.3.1"]
    assert "4xx/5xx" in post["text"] and "204" in post["text"]     # the table, whole
    assert "Data type definitions" not in post["text"]              # not the next clause
    assert post["spec"] == "28532-i00" and post["parts"] == 1


def test_a_chunk_starts_with_its_nearest_parents():
    post = _by_clause(chunk_spec_sections(SPEC))["12.3.1.2.3.2.1.3.1"]
    assert post["text"].startswith(
        '12.3.1.2.3.2.1 Resource \\"/notificationSink\\" › 12.3.1.2.3.2.1.3 HTTP methods')
    assert post["path"].endswith("12.3.1.2.3.2.1.3 HTTP methods › 12.3.1.2.3.2.1.3.1 POST")
    assert "{#post-4" not in post["text"]


def test_parent_headings_and_contents_are_not_chunks():
    clauses = {s["clause"] for s in chunk_spec_sections(SPEC)}
    assert "12.3.1.2.3" not in clauses and "12.3.1.2.3.2.1.3" not in clauses   # no own text
    assert not any(s["title"] == "Contents" for s in chunk_spec_sections(SPEC))


def test_annexes_and_the_cover_page_are_left_out():
    segs = _by_clause(chunk_spec_sections(SPEC))
    assert "A.1" not in segs and "A" not in segs
    assert not any("openapi: 3.0.1" in s["text"] for s in segs.values())
    assert not any(s["title"] == "(front matter)" for s in segs.values())
    assert segs["12.3.1.2.4"]["annex"] is False                      # the text before stays


def test_annexes_can_be_asked_for_and_are_marked():
    segs = _by_clause(chunk_spec_sections(SPEC, include_annexes=True))
    assert segs["A.1"]["annex"] is True and segs["1"]["annex"] is False


def test_a_long_section_is_divided_and_each_part_keeps_its_heading():
    body = "\n\n".join(f"Paragraph {i} " + "word " * 60 for i in range(40))
    segs = chunk_spec_sections(f"# 4 Concepts\n\n{body}", max_chars=1000)
    assert len(segs) > 1 and all(len(s["text"]) <= 1000 for s in segs)
    assert all(s["text"].startswith(f"# 4 Concepts (part {s['part']}/{len(segs)})")
               for s in segs)


@pytest.mark.parametrize("title, clause", [
    ("12.1.1.4.1a.7 Type NotifyMoiAttributeValueChanges", "12.1.1.4.1a.7"),
    ("A.1.1 OpenAPI document", "A.1.1"),
    ("Annex A (normative): OpenAPI specification", "A"),
    ("Foreword", ""),
])
def test_clause_of(title, clause):
    assert clause_of(title) == clause


DATASET = os.path.join(os.path.dirname(__file__), "..", "..", "..", "Dataset", "TSpec-LLM",
                       "3GPP-clean", "Rel-18", "28_series", "28532-i00.md")


@pytest.mark.skipif(not os.path.exists(DATASET), reason="TSpec-LLM dataset not present")
def test_the_real_spec_keeps_its_tables_with_their_clause():
    from qdrant_3gpp_rag.index_3gpp_all import _clean_markdown

    with open(DATASET, encoding="utf-8") as f:
        text = _clean_markdown(f.read())
    segs = chunk_spec_sections(text)
    by = _by_clause(segs)
    assert "4xx/5xx" in by["12.3.1.2.3.2.1.3.1"]["text"]                 # POST /notificationSink
    t = by["12.3.1.2.4.2.1"]["text"]                                    # Type NotifyThresholdCrossing
    assert "hysteresis" in t and "monitorGranularityPeriod" in t
    assert not any(s["annex"] for s in segs)                            # no annex at all
    assert not any("openapi:" in s["text"] for s in segs)
    assert len(split_sections(text)) > 600
