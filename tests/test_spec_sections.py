"""Chunking a 3GPP spec by section (spec_sections) — one chunk per clause."""

import os

import pytest

from qdrant_3gpp_rag.spec_sections import (
    _CODE_SHAPE,
    chunk_spec_sections,
    clause_ancestors,
    clause_of,
    defines_of,
    split_sections,
)

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


def test_clause_ancestors_with_a_letter_suffix():
    assert clause_ancestors("12.1.1.4.1a.7") == [
        "12", "12.1", "12.1.1", "12.1.1.4", "12.1.1.4.1a", "12.1.1.4.1a.7"]
    assert clause_ancestors("") == []


@pytest.mark.parametrize("title, name", [
    ("12.3.1.2.4.2.1 Type NotifyThresholdCrossing", "NotifyThresholdCrossing"),
    ("5.2.4.4 Type: ReplaceHttpUrl", "ReplaceHttpUrl"),
    ("12.3.1.2.4.3.3 Enumeration PerfNotificationTypes", "PerfNotificationTypes"),
    ("5.2.3.1 Enumeration: PatchOperation", "PatchOperation"),
    ("11.3.1.3 Notification notifyThresholdCrossing", "notifyThresholdCrossing"),
    ('12.5.1.1.2 Operation \\"establishStreamingConnection\\"', "establishStreamingConnection"),
    ('12.3.1.2.3.2.1 Resource \\"/notificationSink\\"', "/notificationSink"),
    ("6.1.3.3 Resource: Individual SM Context (Document)", "Individual SM Context"),
    ("8.5.2.3.4.3 Operation: relocate (POST)", "relocate"),
    ("12.2.1.2.2 Notification notifyNewAlarm (non-security alarm)", "notifyNewAlarm"),
])
def test_defines_reads_the_name_from_a_3gpp_title(title, name):
    assert defines_of(title) == [name]


@pytest.mark.parametrize("title", [
    "12.1.1.3.1 Resource structure",
    "6.1.3.2.3 Resource Standard Methods",
    "4.4.2 Resource allocation",
    "17 Notification Management",
    "4.2.4.3 Notification PUSH",
    "6 Operation Points",
    "5.10.6.2 Operation modes",
    "12.3.1.2.3.2.1.3.1 POST",
    "Foreword",
])
def test_a_title_that_defines_nothing(title):
    assert defines_of(title) == []


MENTIONS_SPEC = """\
# 5 Types

## 5.1 Type NotifyThresholdCrossing

| Attribute | Data type |
| thresholdInfo | ThresholdInfo |
| direction | PerfMetricDirection |

## 5.2 Type ThresholdInfo

Holds a threshold. Not to be confused with NotifyThresholdCrossing.

## 5.3 Enumeration: PerfMetricDirection

Values: INCREASING, DECREASING. Used together with ExternalType from TS 29.571.

## 5.4 Type Scope

Scope of a request.
"""


def test_mentions_are_the_names_defined_in_the_same_spec():
    by = _by_clause(chunk_spec_sections(MENTIONS_SPEC))
    assert by["5.1"]["defines"] == ["NotifyThresholdCrossing"]
    assert by["5.1"]["mentions"] == ["ThresholdInfo", "PerfMetricDirection"]
    assert by["5.2"]["mentions"] == ["NotifyThresholdCrossing"]
    assert by["5.3"]["mentions"] == []          # ExternalType is not defined here
    assert by["5.4"]["defines"] == []           # a bare ordinary word is not a name
    assert by["5.1"]["clause_ancestors"] == ["5", "5.1"]


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
    assert by["12.3.1.2.4.2.1"]["defines"] == ["NotifyThresholdCrossing"]
    assert "NotifyThresholdCrossing" in by["12.3.1.2.3.2.1.3.1"]["mentions"]  # POST -> its body type
    assert "12.3" in by["12.3.1.2.3.2.1.3.1"]["clause_ancestors"]


CORPUS = os.path.join(os.path.dirname(__file__), "..", "..", "..", "Dataset", "TSpec-LLM",
                      "3GPP-clean")


@pytest.mark.skipif(not os.path.isdir(CORPUS), reason="TSpec-LLM dataset not present")
def test_every_bare_define_in_the_corpus_has_the_shape_of_a_code_name():
    """The rule must hold for any release, without a per-spec list: a name written
    after a kind with no colon nor quotes ("Type X") is taken only if it looks like
    code. Run over the OpenAPI-bearing series (28, 29) of every release present."""
    from qdrant_3gpp_rag.index_3gpp_all import load_3gpp_specs

    bad, total = [], 0
    for doc in load_3gpp_specs(CORPUS, series=["28_series", "29_series"]):
        for sec in split_sections(doc["processed_text"]):
            title = sec.title.replace('\\"', '"')
            for name in defines_of(sec.title):
                total += 1
                bare = '"' not in title and ":" not in title.split(name)[0]
                if bare and (" " in name or not _CODE_SHAPE.search(name)):
                    bad.append((doc["spec"], sec.title))
    assert total > 1000 and not bad, bad[:20]
