from concrete_rag.ingest.layout import page_lines
from concrete_rag.parse.is_code import ISCodeParser, extract_xrefs
from concrete_rag.schemas import PageText, SourceDoc, Word


def _words(lines):
    out = []
    for y, segs in lines:
        for x, text in segs:
            for i, t in enumerate(text.split()):
                out.append(Word(text=t, x0=x + i * 30, y0=y, x1=x + i * 30 + 25, y1=y + 8))
    return out


def test_two_column_reading_order():
    lines = []
    for i in range(12):
        lines.append((100 + i * 12, [(50, f"left{i} aa bb"), (320, f"right{i} cc dd")]))
    pg = PageText(doc_id="d", page=1, method="ocr", text="", width=595, height=842, words=_words(lines))
    texts = [ln.text for ln in page_lines(pg)]
    assert texts.index("left11 aa bb") < texts.index("right0 cc dd")


def test_clause_table_parsing():
    doc = SourceDoc(doc_id="T", path="x.pdf", doc_type="is_code", code_no="IS 999", title="t", revision_year=2000)
    body = [
        "1 SCOPE", "1.1 This standard covers testing.", "2 GENERAL REQUIREMENTS", "2.1 Cover",
        "The cover shall be as given in Table 1 and see 1.1.", "20 mm thick plates are used.",
        "Table 1 Nominal Cover Values", "Mild 20", "Severe 45", "NOTES", "1 Values are minimum.",
        "2.2 Spacing shall not exceed 300 mm.",
    ]
    lines = [(100 + i * 12, [(50, t)]) for i, t in enumerate(body)]
    pg = PageText(doc_id="T", page=5, method="text_layer", text="", width=595, height=842, words=_words(lines))
    p = ISCodeParser(doc, two_column=False)
    p.feed_page(pg)
    units = p.finish()
    clauses = [u.number for u in units if u.kind == "clause"]
    assert clauses == ["1.1", "2.1", "2.2"]               # '20 mm' and NOTE '1' rejected
    tbl = [u for u in units if u.kind == "table"][0]
    assert tbl.number == "1" and tbl.notes and len(tbl.table_rows) == 2


def test_xref_targets_other_standard():
    refs = extract_xrefs("as per Table 5 of IS 456 and Table 2, see 5.4.1", "IS 10262", {"5.4.1"})
    assert "IS 456:table:5" in refs and "IS 10262:table:2" in refs and "IS 10262:cl:5.4.1" in refs
