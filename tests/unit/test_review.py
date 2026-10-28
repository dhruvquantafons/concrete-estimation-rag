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


def test_dar_dsr_decisions(tmp_path, monkeypatch):
    """'dar' copies the DAR rate to the DSR item; 'dsr' detaches the DAR analysis; 'other' needs a rate."""
    import pytest
    from sqlalchemy import insert

    from concrete_rag.store import docstore, review
    monkeypatch.setattr(docstore, "engine", lambda e=docstore.create_engine(f"sqlite:///{tmp_path / 'd.db'}"):
                        (docstore.md.create_all(e), e)[1])
    monkeypatch.setattr(review, "engine", docstore.engine)
    monkeypatch.setattr(review, "corrections_path", lambda sor="CPWD-DSR", year=2023: tmp_path / f"{sor}.yaml")
    monkeypatch.setattr(review, "_refresh_item_chunk", lambda *a, **k: None)
    with docstore.engine().begin() as c:
        for code, dsr, dar in (("1.1", 197.35, 117.35), ("1.2", 50.0, 70.0)):
            item = {"item_key": f"CPWD-DSR-2023:{code}", "sor_name": "CPWD-DSR", "sor_year": 2023, "code": code,
                    "rate": dsr, "unit": "kg", "doc_id": "D", "page": 1, "description": "x", "full_description": "x",
                    "needs_review": False, "review_reasons": []}
            c.execute(insert(docstore.sor_items).values(item_key=item["item_key"], sor_name="CPWD-DSR", sor_year=2023,
                                                        code=code, unit="kg", rate=dsr, doc_id="D", needs_review=False,
                                                        data=item))
            a = {"key": f"CPWD-DAR-2023:{code}", "sor_name": "CPWD-DAR", "sor_year": 2023, "code": code, "rate": dar,
                 "doc_id": "A", "page_start": 2, "page_end": 2, "full_description": "x", "lines": [], "steps": [],
                 "needs_review": False, "review_reasons": [], "dsr_match": False}
            c.execute(insert(docstore.dar_analyses).values(key=a["key"], code=code, rate=dar, doc_id="A",
                                                           needs_review=False, dsr_match=False, data=a))
    assert review.list_dar_differences()["total"] == 2
    review.resolve_dar_difference("CPWD-DAR-2023:1.1", "dar", "tester")
    assert docstore.get_sor_item("1.1")["rate"] == 117.35
    with pytest.raises(ValueError):
        review.resolve_dar_difference("CPWD-DAR-2023:1.2", "other", "tester")          # no rate given
    review.resolve_dar_difference("CPWD-DAR-2023:1.2", "dsr", "tester")
    assert docstore.get_sor_item("1.2")["rate"] == 50.0 and docstore.get_dar_analysis("1.2") is None
    assert review.list_dar_differences()["total"] == 0
    assert yaml.safe_load((tmp_path / "CPWD-DAR.yaml").read_text())["items"]["CPWD-DAR-2023:1.2"]["not_for_dsr"]
