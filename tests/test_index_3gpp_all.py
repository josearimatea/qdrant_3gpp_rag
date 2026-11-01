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


class TestReplaceSpec:

    def _tree(self, tmp_path, name="28532-i20"):
        d = tmp_path / "Rel-18" / "28_series"
        d.mkdir(parents=True)
        (d / f"{name}.md").write_text("# 1 Scope\n\nThe new text.", encoding="utf-8")
        (d / "28623-i40.md").write_text("# 1 Scope\n\nAnother spec.", encoding="utf-8")
        return str(tmp_path)

    def test_only_that_spec_is_indexed_and_scoped_and_the_cache_keeps_the_rest(self, tmp_path, monkeypatch):
        import qdrant_3gpp_rag.index_3gpp_all as mod
        from unittest.mock import MagicMock

        calls = []
        monkeypatch.setattr(mod, "index_segments", lambda segs, **kw: calls.append((segs, kw)) or len(segs))
        out = tmp_path / "out"
        cache = mod._chunk_cache_path("c", str(out))
        mod._save_segments_cache(cache, [{"text": "old", "spec_number": "28532", "spec": "28532-i00"},
                                         {"text": "other", "spec_number": "28623", "spec": "28623-i40"}])

        mod.index_3gpp_all(self._tree(tmp_path), "c", factory=MagicMock(), output_dir=str(out),
                           replace_spec="28532")

        segs, kw = calls[0]
        assert {s["spec"] for s in segs} == {"28532-i20"}
        assert kw["scope"] == {"spec_number": "28532"} and kw["prune"] and not kw["recreate"]
        assert {s["spec"] for s in mod._load_segments_cache(cache)} == {"28532-i20", "28623-i40"}

    def test_a_dry_run_leaves_the_cache_alone(self, tmp_path, monkeypatch):
        import qdrant_3gpp_rag.index_3gpp_all as mod
        from unittest.mock import MagicMock

        monkeypatch.setattr(mod, "index_segments", lambda segs, **kw: len(segs))
        out = tmp_path / "out"
        cache = mod._chunk_cache_path("c", str(out))
        mod._save_segments_cache(cache, [{"text": "old", "spec_number": "28532", "spec": "28532-i00"}])

        mod.index_3gpp_all(self._tree(tmp_path), "c", factory=MagicMock(), output_dir=str(out),
                           replace_spec="28532", dry_run=True)

        assert {s["spec"] for s in mod._load_segments_cache(cache)} == {"28532-i00"}

    def test_a_spec_not_under_the_directory_is_an_error(self, tmp_path):
        from unittest.mock import MagicMock

        from qdrant_3gpp_rag.index_3gpp_all import index_3gpp_all

        with pytest.raises(ValueError, match="99999"):
            index_3gpp_all(self._tree(tmp_path), "c", factory=MagicMock(),
                           output_dir=str(tmp_path / "out"), replace_spec="99999")
