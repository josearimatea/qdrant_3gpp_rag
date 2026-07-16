"""Tests for the 3GPP spec loader and chunker (index_3gpp_all)."""

import os

from qdrant_3gpp_rag.index_3gpp_all import (
    _flatten_markdown,
    build_3gpp_segments,
    load_3gpp_specs,
)


class TestFlattenMarkdown:

    def test_table_becomes_plain_text(self):
        md = "| A | B |\n|---|---|\n| 1 | 2 |"
        out = _flatten_markdown(md)
        # Cell values survive as readable text.
        for token in ("A", "B", "1", "2"):
            assert token in out

    def test_blank_lines_collapsed(self):
        out = _flatten_markdown("para1\n\n\n\n\npara2")
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


class TestBuild3gppSegments:

    def test_segments_carry_metadata(self):
        specs = [{
            "release": "Rel-18", "series": "28_series", "spec": "28532-i00",
            "processed_text": "# H\n" + ("word " * 400),  # long enough to split
        }]
        segments = build_3gpp_segments(specs)
        assert len(segments) >= 1
        for seg in segments:
            assert seg["release"] == "Rel-18"
            assert seg["series"] == "28_series"
            assert seg["spec"] == "28532-i00"
            assert seg["text"].strip()
