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


def test_checked_equations_attach_to_clause_chunks():
    from concrete_rag.chunking.chunker import chunk_is_code
    from concrete_rag.parse.is_code import Unit
    doc = SourceDoc(doc_id="IS456_2000", path="x.pdf", doc_type="is_code", code_no="IS 456", title="t",
                    revision_year=2000)
    units = [Unit(kind="clause", number="26.2.1", title="Development Length of Bars", page_start=55, page_end=55,
                  lines=["The development length L, is given by L = 95s where ¢ = nominal diameter of the bar"]),
             Unit(kind="clause", number="40.1", title="Nominal Shear Stress", page_start=85, page_end=85,
                  lines=["The nominal shear stress ... V. . t s—t where"])]
    ld, tau = chunk_is_code(doc, units)
    assert "L = 95s" in ld.text and "Ld = φ σs / (4 τbd)" in ld.text      # OCR kept, clean form added
    assert ld.metadata.equations == ["is456_26_2_1_ld"] and ld.metadata.has_equation
    assert "Nominal shear stress τv = Vu / (b d)" in tau.text and tau.metadata.equations == ["is456_40_1_tau_v"]


def test_checked_table_values_added_to_garbled_table_chunk():
    from concrete_rag.chunking.equations import table_blocks
    blk = table_blocks()["IS456_2000:table:20"]
    assert "M30: 3.5" in blk and "M40 and above: 4.0" in blk and "AI-checked" in blk
    assert "M25: 1.4" in table_blocks()["IS456_2000:cl:26.2.1.1"]


def test_column_word_beside_table_row_is_not_full_width():
    # IS 456 p.85: "direction." ends a left-column paragraph on the baseline of a right-column table row.
    # It used to be classed as a full-width row, so the right column was read before the rest of the left.
    lines = [(100 + i * 12, [(50, f"left{i} aa bb"), (320, f"right{i} cc dd")]) for i in range(10)]
    lines.append((230, [(50, "direction."), (320, "of"), (345, "Slab"), (380, "more"), (500, "less")]))
    lines += [(242 + i * 12, [(50, f"tail{i} ee ff")]) for i in range(3)]
    pg = PageText(doc_id="d", page=1, method="ocr", text="", width=595, height=842, words=_words(lines))
    out = page_lines(pg)
    texts = [ln.text for ln in out]
    assert all(ln.kind != "full" for ln in out)
    assert texts.index("tail2 ee ff") < texts.index("right0 cc dd")      # whole left column first


def test_annex_clause_number_with_ocr_comma():
    doc = SourceDoc(doc_id="T", path="x.pdf", doc_type="is_code", code_no="IS 999", title="t", revision_year=2000)
    body = ["1 SCOPE", "1.1 This standard covers design.", "ANNEX G", "(Clause 38.1)",
            "MOMENTS OF RESISTANCE FOR RECTANGULAR SECTIONS",
            "G-0 The moments of resistance are given in this annex.", "G-1 RECTANGULAR SECTIONS", "G-1,1 Sections Without Compression Reinforcement",
            "The moment of resistance shall be obtained as follows.", "G-1.2 Section with Compression Reinforcement",
            "Where the moment exceeds the limiting value."]
    lines = [(100 + i * 12, [(50, t)]) for i, t in enumerate(body)]
    pg = PageText(doc_id="T", page=5, method="text_layer", text="", width=595, height=842, words=_words(lines))
    p = ISCodeParser(doc, two_column=False)
    p.feed_page(pg)
    assert [u.number for u in p.finish() if u.kind == "annex_clause"] == ["G-0", "G-1.1", "G-1.2"]   # G-1 is a heading only


def test_full_width_prose_on_digital_page_is_not_split(monkeypatch):
    # IS 10262 B-0: one sentence runs across both columns; ordinary word spacing happens to sit on the gutter
    monkeypatch.setattr("concrete_rag.ingest.layout.find_gutter", lambda lines, width: 293.5)
    def col(y, x0, x1, tag):
        return [Word(text=f"{tag}{i}", x0=x, y0=y, x1=x + 40, y1=y + 8) for i, x in enumerate(range(x0, x1 - 39, 45))]
    words = []
    for i in range(12):
        words += col(100 + i * 12, 60, 286, f"L{i}w") + col(100 + i * 12, 305, 525, f"R{i}w")
    words += [Word(text="for", x0=262, y0=250, x1=282, y1=258), Word(text="a", x0=285, y0=250, x1=292, y1=258),
              Word(text="concrete", x0=295, y0=250, x1=335, y1=258)]
    for method, joined in (("text_layer", True), ("ocr", False)):     # OCR'd scans: column edges too ragged
        pg = PageText(doc_id="d", page=1, method=method, text="", width=595, height=842, words=words)
        assert ("for a concrete" in [ln.text for ln in page_lines(pg)]) is joined, method
