from concrete_rag.parse.sor import SorParseResult
from concrete_rag.parse.sor_clean import parse_items
from concrete_rag.parse.sor_merge import merge_clean_copy
from concrete_rag.schemas import PageText, SorItem, SourceDoc, Word

DOC = SourceDoc(doc_id="HQ", path="x.pdf", doc_type="sor_reference", code_no="CPWD DSR", title="t", revision_year=2023,
                sor_name="CPWD-DSR", region="Delhi")


def _page(rows):
    words, y = [], 80.0
    for row in [[(58, "Code"), (84, "No"), (236, "Description"), (437, "Unit"), (489, "Rate")]] + rows:
        for x, t in row:
            words.append(Word(text=t, x0=x, y0=y, x1=x + 5.5 * len(t), y1=y + 8))
        y += 14
    text = "13.0 FINISHING\n" + "\n".join(" ".join(t for _, t in r) for r in rows)
    return PageText(doc_id="HQ", page=7, method="text_layer", width=594, height=792, words=words, text=text)


def test_clean_reader_rows_wraps_percentages_and_notes():
    rows = [
        [(107, "Note"), (130, ":-"), (138, "Rates"), (165, "for"), (180, "external"), (230, "plaster")],
        [(107, "CEMENT"), (150, "PLASTER")],
        [(58, "13.1"), (107, "12"), (120, "mm"), (139, "cement"), (173, "plaster"), (214, "of"), (229, "mix")],
        [(107, "13.1.1"), (158, "1:4"), (174, "(1"), (185, "cement:"), (222, "4"), (230, "fine"), (247, "sand)"),
         (437, "sqm"), (485, "347.05")],
        [(107, "13.1.2"), (158, "Cement"), (200, "plaster"), (245, "finished"), (300, "with"), (330, "a")],
        [(158, "floating"), (205, "coat"), (437, "sqm"), (485, "333.35")],
        [(58, "13.2"), (107, "Extra"), (140, "for"), (160, "height"), (437, "20%")],
    ]
    recs, notes, groups = parse_items(DOC, [_page(rows)])
    by = {r.code: r for r in recs}
    assert by["13.1.1"].rate == "347.05" and by["13.1.1"].unit == ["sqm"] and by["13.1.1"].group == "Cement Plaster"
    assert by["13.1.2"].rate == "333.35" and by["13.1.2"].desc[-1] == "coat"          # wrapped description
    assert by["13.2"].pct == "20%" and by["13.2"].rate is None                         # not a rupee rate
    assert groups["13.1"].startswith("12 mm cement plaster") and notes[0]["text"].startswith("Note")


def _item(code, rate, doc="SCAN", page=5, desc="scan text", key=None):
    return SorItem(item_key=key or f"CPWD-DSR-2023:{code}", sor_name="CPWD-DSR", sor_year=2023, doc_id=doc, code=code,
                   raw_code=code, description=desc, full_description=desc, unit="m2", rate=rate, page=page,
                   rate_votes=2, rate_readings=[str(rate)])


def _res(items, coeffs=()):
    return SorParseResult(items=list(items), basic_rates=[], cement_coefficients=list(coeffs), notes=[], stats={})


def test_merge_rules(monkeypatch):
    monkeypatch.setattr("concrete_rag.parse.sor_merge._dar_rate", lambda code: None)   # no DAR analysis
    scan = _res([_item("13.1.1", 347.05), _item("13.10.0.1", 96.0), _item("16.54.1", 1129.55),
                 _item("26.50.1A", 1635.05), _item("9.9", 50.0)],
                [{"code": "3.1", "cement_quintal_per_unit": 9.0, "doc_id": "SCAN"}])
    clean = _res([_item("13.1.1", 347.05, "HQ", 12, "clean text"), _item("13.100.1", 96.0, "HQ", 30),
                  _item("16.54.1", 11129.55, "HQ", 40), _item("13.200.1", 55.0, "HQ", 41), _item("9.9", 60.0, "HQ")],
                 [{"code": "3.1", "cement_quintal_per_unit": 10.2, "doc_id": "HQ", "unit": "m3"}])
    corrections = {"CPWD-DSR-2023:9.9": {"rate": 50.0, "checked_on": "2026-10-20"},          # read on the scan
                   "CPWD-DSR-2023:13.1.1": {"rate": 350.0, "checked_on": "2026-11-02"}}      # override of the clean
    stats = merge_clean_copy(scan, clean, doc_id="SCAN", corrections=corrections)
    by = {i.code: i for i in scan.items}
    a = by["13.1.1"]
    assert a.source == "scan+clean" and a.description == "clean text" and not a.needs_review
    assert a.rate == 350.0                                                                    # fresh correction wins
    assert "13.10.0.1" not in by and by["13.100.1"].item_key == "CPWD-DSR-2023:13.100.1"        # recoded
    d = by["16.54.1"]
    assert d.rate == 11129.55 and d.scan_rate == 1129.55 and d.source == "clean_over_scan"   # clean copy wins,
    assert d.needs_review and "check the clean copy" in d.review_reasons[-1]                  # unconfirmed: listed
    added = by["13.200.1"]
    assert added.source == "clean" and added.doc_id == "SCAN" and added.clean_doc_id == "HQ"
    s = by["26.50.1A"]
    assert s.source == "scan" and s.needs_review and "not in the clean copy" in s.review_reasons[0]
    assert by["9.9"].rate == 60.0 and by["9.9"].source == "clean_over_scan"                   # beats an older check
    assert stats["old_corrections_overridden_by_clean"] == [{"item_key": "CPWD-DSR-2023:9.9", "rate": "50.0 -> 60.0"}]
    assert stats["recoded"] == 1 and stats["added_from_clean"] == 1
    [co] = scan.cement_coefficients
    assert co["cement_quintal_per_unit"] == 10.2 and co["doc_id"] == "SCAN"
