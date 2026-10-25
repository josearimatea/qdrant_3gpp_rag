"""Tests for the 3GPP spec loader and chunker (index_3gpp_all)."""

import os

import pytest

from qdrant_3gpp_rag.index_3gpp_all import (
    _clean_markdown,
    build_3gpp_segments,
    load_3gpp_specs,
)


class TestCleanMarkdown:

    def test_headings_and_tables_stay_markdown(self):
        md = "##### 12.1 POST {#post-4 .H6}\n\n| A | B |\n|---|---|\n| 1 | 2 |"
        out = _clean_markdown(md)
        assert out.startswith("##### 12.1 POST\n")          # marker kept, attrs dropped
        assert "| 1 | 2 |" in out

    def test_blank_lines_collapsed(self):
        out = _clean_markdown("para1\n\n\n\n\npara2")
        assert "\n\n\n" not in out
        assert "para1" in out and "para2" in out


class TestLoad3gppSpecs:

    def _make_tree(self, tmp_path):
        # release/series/spec.md layout
        d = tmp_path / "Rel-18" / "28_series"
        d.mkdir(parents=True)
        (d / "28532-i00.md").write_text("# Scope\nProvisioning management service.", encoding="utf-8")
        other = tmp_path / "Rel-18" / "32_series"
        other.mkdir(parents=True)
        (other / "32160-i00.md").write_text("# Concepts\nSome content.", encoding="utf-8")
        return str(tmp_path)

    def test_loads_all_by_default(self, tmp_path):
        root = self._make_tree(tmp_path)
        specs = load_3gpp_specs(root)
        specs_by_name = {s["spec"] for s in specs}
        assert specs_by_name == {"28532-i00", "32160-i00"}
        s = next(x for x in specs if x["spec"] == "28532-i00")
        assert s["release"] == "Rel-18" and s["series"] == "28_series"
        assert "Provisioning" in s["processed_text"]

    def test_series_filter(self, tmp_path):
        root = self._make_tree(tmp_path)
        specs = load_3gpp_specs(root, series=["28_series"])
        assert [s["spec"] for s in specs] == ["28532-i00"]

    def test_release_filter_excludes_others(self, tmp_path):
        root = self._make_tree(tmp_path)
        assert load_3gpp_specs(root, releases=["Rel-17"]) == []

    def test_spec_filter(self, tmp_path):
        root = self._make_tree(tmp_path)
        assert [s["spec"] for s in load_3gpp_specs(root, specs=["32160-i00"])] == ["32160-i00"]


class TestBuild3gppSegments:

    def test_segments_carry_metadata(self):
        specs = [{
            "release": "Rel-18", "series": "28_series", "spec": "28532-i00",
            "file": "Rel-18/28_series/28532-i00.md",
            "processed_text": "# 1 Scope\n" + ("word " * 400),
        }]
        segments = build_3gpp_segments(specs)
        assert len(segments) >= 1
        for seg in segments:
            assert seg["release"] == "Rel-18"
            assert seg["series"] == "28_series"
            assert seg["spec"] == "28532-i00"
            assert seg["text"].strip()
            assert seg["clause"] == "1" and seg["chunking"] == "sections-v3"
            assert seg["spec_number"] == "28532" and seg["version"] == "i00"
            assert seg["file"] == "Rel-18/28_series/28532-i00.md"


@pytest.mark.parametrize("stem, number, version", [
    ("28532-i00", "28532", "i00"),
    ("38101-1-i30_s00-05", "38101-1", "i30"),      # one spec split over several files
    ("28541-i50_AnnexesA_D", "28541", "i50"),
    ("22.890-050(cl)", "22890", "050"),
    ("36.521-1 8.3.1.1.4 TT", "36521-1", ""),      # named after its spec, no version
    ("10.2 TT", "", ""),
])
def test_spec_identity(stem, number, version):
    from qdrant_3gpp_rag.index_3gpp_all import spec_identity

    assert spec_identity(stem) == {"spec_number": number, "version": version}
