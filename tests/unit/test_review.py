import yaml

from concrete_rag.store.review import _write_correction


def test_correction_file_insert_replace_keeps_comments(tmp_path):
    p = tmp_path / "c.yaml"
    p.write_text("# header comment\nitems:\n  # keep me\n  \"K:1\": {rate: 1.0}\n")
    _write_correction("K:2", {"rate": 2.5, "checked_by": "a"}, p)
    _write_correction("K:1", {"exclude": True, "checked_by": "b"}, p)
    text = p.read_text()
    assert "# header comment" in text and "# keep me" in text
    items = yaml.safe_load(text)["items"]
    assert items["K:1"] == {"exclude": True, "checked_by": "b"}
    assert items["K:2"]["rate"] == 2.5
