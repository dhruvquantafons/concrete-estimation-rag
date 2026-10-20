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


def test_write_correction_replaces_wrapped_entry(tmp_path):
    import yaml
    from concrete_rag.store.review import _prior_entry, _write_correction
    p = tmp_path / "c.yaml"
    # an entry wrapped over several lines by the old writer, followed by another entry
    p.write_text('items:\n  "K:1": {rate: 1.0, note: a long note that the old writer\n    wrapped onto the next line}\n'
                 '  "K:2": {rate: 2.0}\n')
    _write_correction("K:1", {**_prior_entry("K:1", p), "rate": 5.0, "checked_by": "t"}, p)
    items = yaml.safe_load(p.read_text())["items"]
    assert items["K:1"]["rate"] == 5.0 and items["K:1"]["note"].startswith("a long note") and items["K:2"]["rate"] == 2.0
    _write_correction("K:3", {"note": "x " * 80, "checked_by": "t"}, p)
    assert len(p.read_text().splitlines()) == 4          # one physical line per entry, never wrapped


def test_prior_entry_keeps_earlier_code_and_unit(tmp_path):
    from concrete_rag.store.review import _prior_entry
    p = tmp_path / "c.yaml"
    p.write_text('items:\n  "K:1": {rate: 1.0, unit: m3, code: "11.43", checked_by: a, checked_on: "2026-01-01"}\n')
    assert _prior_entry("K:1", p) == {"rate": 1.0, "unit": "m3", "code": "11.43"}
    assert _prior_entry("missing", p) == {}
