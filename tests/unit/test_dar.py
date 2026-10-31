import pytest

from concrete_rag.parse.dar import excluded_markers, line_category, parse_analyses
from concrete_rag.schemas import PageText, SourceDoc, Word
from tests.conftest import needs_corpus

DOC = SourceDoc(doc_id="T", path="x.pdf", doc_type="dar", code_no="CPWD DAR", sor_name="CPWD-DAR", region="Delhi",
                title="CPWD Delhi Analysis of Rates 2023 - 14.09.2023", revision_year=2023)
COLS = {"Code": 58, "Description": 177, "Unit": 312, "Quantity": 346, "Rate": 404, "Amount": 458}


def _page(rows: list[list[tuple[float, str]]]) -> PageText:
    """Rows of (x, text) laid out like a DAR page; numbers get a realistic width (right-aligned columns)."""
    words, y = [], 60.0
    for row in [[(x, t) for t, x in COLS.items()]] + rows:
        for x, t in row:
            words.append(Word(text=t, x0=x, y0=y, x1=x + 5.2 * len(t), y1=y + 8))
        y += 13
    words.append(Word(text="SUB", x0=54, y0=724, x1=70, y1=732))      # footer: sub-head 5
    words += [Word(text=t, x0=x, y0=724, x1=x + 30, y1=732) for x, t in ((76, "HEAD"), (104, ":"), (109, "5"),
                                                                       (117, "REINFORCED"))]
    return PageText(doc_id="T", page=1, method="text_layer", width=581, height=774, words=words,
                    text="SUB HEAD : 5 REINFORCED CEMENT CONCRETE")


def test_parse_dar_analysis_and_checks():
    rows = [
        [(57, "5.33.2.1"), (105, "Concrete"), (149, "of"), (160, "M25"), (181, "grade")],
        [(106, "Details"), (138, "of"), (149, "cost"), (169, "for"), (182, "1.00"), (203, "cum")],
        [(106, "MATERIAL")],
        [(58, "0367"), (106, "Cement"), (309, "tonne"), (366, "0.33"), (408, "5156.00"), (472, "1701.48")],
        [(58, "0009"), (106, "Pumping"), (312, "cum"), (366, "1.00"), (413, "250.00"), (477, "250.00"), (518, "P")],
        [(106, "Extra"), (131, "piping"), (240, "="), (249, "100%"), (276, "of"), (286, "P"), (477, "250.00")],
        [(58, "3.8"), (106, "Rate"), (129, "as"), (142, "per"), (158, "Item"), (190, "3.8"), (312, "cum"), (366, "0.10"),
         (404, "4881.95"), (477, "488.20"), (518, "A")],
        [(106, "LABOUR")],
        [(58, "0114"), (106, "Beldar"), (313, "day"), (366, "2.00"), (413, "736.00"), (472, "1472.00")],
        [(106, "TOTAL"), (472, "4161.68"), (516, "W")],
        [(106, "Add"), (126, "1"), (134, "%"), (145, "Water"), (173, "charges"), (209, "on"), (222, "“W-A”"), (482, "36.73")],
        [(106, "TOTAL"), (472, "4198.41"), (518, "X")],
        [(106, "Add"), (126, "GST"), (148, "on"), (161, "“X-A”"), (176, "(multiplying"), (227, "factor"), (254, "0.2127)"),
         (472, "789.17")],
        [(106, "TOTAL"), (472, "4987.58"), (518, "Y")],
        [(106, "Add"), (126, "15%"), (148, "CPOH"), (178, "on"), (191, "“Y-A”"), (472, "674.91")],
        [(106, "TOTAL"), (472, "5662.49"), (518, "Z")],
        [(106, "Add"), (126, "Cess"), (150, "@"), (162, "1%"), (179, "on"), (192, "“Z-A”"), (477, "51.74")],
        [(106, "Cost"), (129, "per"), (145, "1.00"), (166, "cum"), (472, "5714.23")],
        [(106, "Say"), (472, "5714.25")],
    ]
    [a] = parse_analyses(DOC, [_page(rows)])
    assert a.code == "5.33.2.1" and a.rate == 5714.25 and a.unit == "m3" and a.sub_head.startswith("5 ")
    assert [ln.category for ln in a.lines] == ["material", "equipment", "equipment", "sub_analysis", "labour"]
    assert a.lines[2].description.endswith("100% of P")          # uncoded row takes the category of row P
    assert (a.water_percent, a.gst_factor, a.cpoh_percent, a.cess_percent) == (1.0, 0.2127, 15.0, 1.0)
    assert not a.needs_review, a.review_reasons                  # every amount and step checks out


def test_parse_dar_flags_arithmetic_errors():
    rows = [[(57, "4.1.8"), (105, "PCC")], [(106, "Details"), (138, "of"), (149, "cost"), (169, "for"), (182, "1"),
                                            (196, "cum")],
            [(58, "0114"), (106, "Beldar"), (313, "day"), (366, "2.00"), (413, "736.00"), (472, "1400.00")],
            [(106, "TOTAL"), (472, "1400.00"), (516, "W")], [(106, "Say"), (472, "1400.00")]]
    [a] = parse_analyses(DOC, [_page(rows)])
    assert a.needs_review and any("2.0 x 736.0" in r for r in a.review_reasons)


def test_parse_dar_other_openings_and_cost_total_row():
    """5.9.5 opens with "Consider ...", 11.46A ends its build-up with "Details of cost for 133.33 metre <amount>"."""
    rows = [[(57, "5.9.5"), (105, "Shuttering")], [(106, "Consider"), (150, "10"), (165, "beams")],
            [(58, "0114"), (106, "Beldar"), (313, "day"), (366, "2.00"), (413, "736.00"), (472, "1472.00")],
            [(106, "TOTAL"), (472, "1472.00"), (516, "W")],
            [(106, "Details"), (138, "of"), (149, "cost"), (169, "for"), (182, "10"), (196, "metre"), (472, "1472.00")],
            [(106, "Cost"), (129, "per"), (145, "metre"), (477, "147.20")], [(106, "Say"), (477, "147.20")]]
    [a] = parse_analyses(DOC, [_page(rows)])
    assert a.code == "5.9.5" and a.rate == 147.2 and a.unit == "m" and len(a.lines) == 1
    assert not a.needs_review, a.review_reasons


def test_excluded_markers_and_categories():
    assert excluded_markers("Add GST on “X-A-P-R” (multiplying factor 0.2127)") == ["A", "P", "R"]
    assert excluded_markers("Add 1 % Water charges on “W”") == []
    assert line_category("0114", "Beldar", None) == "labour"
    assert line_category("0012", "Vibrator (needle type 40mm)", None) == "equipment"
    assert line_category("9999", "Hire and running charges of mechanical mixer", None) == "equipment"
    assert line_category("2209", "Carriage of Cement", None) == "carriage"
    assert line_category("5/Sub AR-1", "Rate as per Sub AR-1", None) == "sub_analysis"


def test_rate_analysis_follows_dar_build_up(monkeypatch):
    from concrete_rag.calc import rates
    real = rates._cfg
    monkeypatch.setattr(rates, "_cfg", lambda name: {**real(name), "templates": {"t": {"unit": "m3", "materials": {"X": 1}}}}
                        if name == "rate_analysis" else real(name))
    monkeypatch.setattr(rates.docstore, "get_basic_rate", lambda code: {"rate": 7105.56, "description": "all",
                                                                         "unit": "m3", "needs_review": False})
    r = rates.rate_analysis("t")
    assert r["rate"] == 10108.65                 # DAR p.302: W 7105.56 -> X 7176.62 -> Y 8703.09 -> Z 10008.55 -> Say


@needs_corpus
def test_dar_breakdown_and_reprice_reproduce_dsr_rate():
    from concrete_rag.calc import rates
    from concrete_rag.store import docstore
    if not docstore.get_dar_analysis("5.33.2.1"):
        pytest.skip("DAR not ingested")
    b = rates.dar_breakdown("5.33.2.1")
    assert b["rate"] == 9860.4 and b["dsr_match"]
    assert abs(sum(b["per_unit"].values()) - 9860.4) < 0.02
    assert rates.reprice("5.33.2.1")["rate"] == 9860.4
    up = rates.reprice("5.33.2.1", {"0367": 5156 * 1.1})            # cement +10 %
    assert up["rate"] > 9860.4 and up["changed"][0]["code"] == "0367"
    assert rates.dar_breakdown("13.9.2")["share_percent"]["labour"] > 40   # plaster: mortar sub-analysis expanded
    assert docstore.get_basic_rate("0367")["sor_name"] == "CPWD-DAR"      # digital DAR basic rates come first


def test_basic_rate_units_as_printed():
    from concrete_rag.normalize.units import normalize_unit
    assert normalize_unit("km/cum") == "m3_km" and normalize_unit("tonne km") == "t_km"
    assert normalize_unit("score") == "score" and normalize_unit("sqm of door area") == "m2"
    assert normalize_unit("tonne") == "t"


def test_parse_dar_heading_wrap_suffix_codes_ocr_marks_and_no_say():
    rows = [
        # a heading wrapping onto "7.5 in cement mortar" (at code + 40) is text, not a new item 7.5
        [(57, "6.8"), (98, "Brick"), (125, "work"), (150, "of"), (162, "class"), (190, "designation")],
        [(98, "7.5"), (115, "in"), (126, "cement"), (163, "mortar")],
        [(106, "Details"), (138, "of"), (149, "cost"), (169, "for"), (182, "1"), (196, "sqm")],
        [(58, "0114"), (106, "Beldar"), (313, "day"), (366, "1.00"), (413, "736.00"), (472, "736.00]")],   # OCR "]"
        [(106, "TOTAL"), (472, "736.00"), (516, "W")],
        [(106, "Cost"), (129, "per"), (145, "1"), (156, "sqm"), (472, "736.02")],                         # no 'Say'
        [(57, "4.9"), (73, "A"), (111, "Precasting"), (164, "bollards")],                                 # "4.9 A"
        [(106, "Details"), (138, "of"), (149, "cost"), (169, "for"), (182, "1"), (196, "each")],
        [(58, "0114"), (106, "Beldar"), (313, "day"), (366, "1.00"), (413, "900.15"), (472, "900.15")],
        [(106, "TOTAL"), (472, "900.15"), (516, "W")],
        [(106, "Cost"), (129, "of"), (145, "each"), (472, "900.15")],
        [(106, "Say"), (472, "900.15")],
    ]
    a, b = parse_analyses(DOC, [_page(rows)])
    assert a.code == "6.8" and a.rate == 736.0 and a.lines[0].amount == 736.0   # cost rounded to 0.05, as the DSR
    assert b.code == "4.9A" and b.rate == 900.15
