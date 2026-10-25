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


def test_annexes_are_indexed_and_marked_the_cover_page_is_not():
    segs = _by_clause(chunk_spec_sections(SPEC))
    assert segs["A.1"]["annex"] is True and "openapi: 3.0.1" in segs["A.1"]["text"]
    assert segs["12.3.1.2.4"]["annex"] is False and segs["1"]["annex"] is False
    assert not any(s["title"] == "(front matter)" for s in segs.values())


def test_annexes_can_be_left_out():
    segs = _by_clause(chunk_spec_sections(SPEC, include_annexes=False))
    assert "A.1" not in segs and not any(s["annex"] for s in segs.values())


ANNEX_SPEC = """# 5 Scope

Uses NotificationHeader from the common definitions.

# Annex C (normative): OpenAPI definitions

## C.1 TS28623_ComDefs.yaml

""" + "\n\n".join(f"""{name}:

type: object

properties:

""" + "\n\n".join(f"  field{i}: string" for i in range(60)) for name in
    ("NotificationHeader", "SystemDN", "Uri")) + """

# Annex E (informative): Change history

| Date | Meeting | CR | Subject |
| 2019-12 | SA#86 | 0076 | Add notifyEvent |
"""


def test_yaml_types_in_an_annex_define_themselves_and_every_annex_is_kept():
    segs = chunk_spec_sections(ANNEX_SPEC, max_chars=1200)
    by_def = {}
    for seg in segs:
        for d in seg["defines"]:
            by_def.setdefault(d, []).append(seg)
    hdr = by_def["NotificationHeader"]                 # a long type: every part keeps its name
    assert all(p["annex"] for p in hdr)
    assert hdr[0]["text"].count("NotificationHeader:") == 1
    by_def = {d: parts[0] for d, parts in by_def.items()}
    assert by_def["SystemDN"]["clause"] == "C.1"
    assert "Uri" not in by_def                       # not code-shaped: no define
    assert any("SA#86" in s["text"] and s["annex"] for s in segs)    # change history too
    scope = [s for s in segs if s["clause"] == "5"][0]
    assert "NotificationHeader" in scope["mentions"]  # normative text -> annex type


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
    "8.4.4.2.3 Type: Void",
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


INHERIT_SPEC = """\
#### 11.1.1.10 Notification notifyEvent

##### 11.1.1.10.1 Definition

The notification notifyEvent is sent when an event occurs.

##### 11.1.1.10.2 Input parameters

| eventType | M |

#### 12.3 Resource \\"/x\\"

##### 12.3.1 Type YangInfo

Definition of type YangInfo.

###### 12.3.1.1 Constraints

Only for YangInfo.

##### 12.3.2 HTTP methods

###### 12.3.2.1 POST

Creates an x.

#### 12.4 Data types

##### 12.4.1 General

Nothing defined here.
"""


def test_children_of_a_defining_title_without_text_inherit_its_name():
    by = _by_clause(chunk_spec_sections(INHERIT_SPEC))
    assert by["11.1.1.10.1"]["defines"] == ["notifyEvent"]       # Definition
    assert by["11.1.1.10.2"]["defines"] == ["notifyEvent"]       # Input parameters
    assert by["12.3.2.1"]["defines"] == ["/x"]                   # POST of the resource
    assert by["12.3.1"]["defines"] == ["YangInfo"]                  # its own title wins
    assert by["12.3.1.1"]["defines"] == []     # nearest definer (YangInfo) has text: no jump to /x
    assert by["12.4.1"]["defines"] == []       # "Data types" defines nothing


DATASET = os.path.join(os.path.dirname(__file__), "..", "..", "..", "Dataset", "TSpec-LLM",
                       "3GPP-clean", "Rel-18", "28_series", "28532-i00.md")


COMDEFS = os.path.join(os.path.dirname(DATASET), "28623-i40.md")


@pytest.mark.skipif(not os.path.exists(COMDEFS), reason="TSpec-LLM dataset not present")
def test_the_common_types_of_ts_28623_are_reachable_by_defines():
    from qdrant_3gpp_rag.index_3gpp_all import _clean_markdown

    with open(COMDEFS, encoding="utf-8") as f:
        segs = chunk_spec_sections(_clean_markdown(f.read()))
    hdr = [s for s in segs if "NotificationHeader" in s["defines"]]
    assert hdr and all(s["annex"] for s in hdr)
    assert "systemDN" in hdr[0]["text"] and "eventTime" in hdr[0]["text"]
    names = {d for s in segs for d in s["defines"]}
    assert {"Dn", "Filter", "Uri", "Float"} <= names        # confirmed by its own $refs
    assert not {"Location", "ETag"} & names                 # header keys, not types


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
    assert not any("openapi:" in s["text"] for s in segs if not s["annex"])
    assert any(s["annex"] for s in segs)                                # annexes indexed
    assert any("change history" in s["path"].lower() for s in segs)
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


def test_chunk_spec_sections_is_exported_by_the_package():
    import qdrant_3gpp_rag

    assert "chunk_spec_sections" in qdrant_3gpp_rag.__all__
    assert qdrant_3gpp_rag.chunk_spec_sections is chunk_spec_sections


REF_SPEC = """\
# C.1 TS28623_ComDefs.yaml

""" + "\n\n".join(f"{name}:\n\ntype: string\n\ndescription: " + "word " * 120 for name in
                    ("Uri", "Location", "SystemDN")) + """

NotificationHeader:

properties:

href:

\\$ref: \\'\\#/components/schemas/Uri\\'
"""


def test_a_yaml_name_is_a_type_when_the_spec_refers_to_it_as_one():
    by_def = {d: s for s in chunk_spec_sections(REF_SPEC, max_chars=1000) for d in s["defines"]}
    assert "Uri" in by_def                 # not code-shaped, but '#/components/schemas/Uri'
    assert "Location" not in by_def        # never referenced as a type (a header key)
    assert "SystemDN" in by_def and "NotificationHeader" in by_def


def test_type_names_referenced_from_another_file_of_the_same_spec_count():
    segs = chunk_spec_sections(REF_SPEC.replace("components/schemas/Uri", "nothing"),
                               max_chars=1000, schema_names={"Location"})
    names = {d for s in segs for d in s["defines"]}
    assert "Location" in names and "Uri" not in names


def test_schema_refs_strips_markdown_escapes():
    from qdrant_3gpp_rag.spec_sections import schema_refs

    assert schema_refs("\\$ref: \\'\\#/components/schemas/Uri\\'") == {"Uri"}
