import pytest

from concrete_rag.calc import confidence as cf
from concrete_rag.calc.code_tables import Ref, code_editions
from concrete_rag.calc.mix_design import Admixture, MixDesignInput, design_mix
from concrete_rag.estimate.concrete_mix import ConcreteMixRequest, concrete_mix_report
from concrete_rag.normalize.units import convert_quantity
from tests.conftest import needs_corpus

WR = Admixture(type="water_reducer", dosage_percent=0.8, water_reduction_percent=10)


def test_convert_quantity_scaled_units():
    assert convert_quantity(250, "m2", "10m2") == 25
    assert convert_quantity(1500, "kg", "quintal") == 15
    with pytest.raises(ValueError):
        convert_quantity(1, "m3", "kg")


def test_refs_carry_code_edition_and_amendments():
    assert code_editions()["IS 456"] == "IS 456 : 2000 (incl. Amd 1-3)"
    assert code_editions()["IS 2386 (Part 3)"] == "IS 2386 (Part 3) : 1963 (incl. Amd 1 (2018))"
    assert Ref(code="IS 1200 (Part 2)", item="cl. 5.1.8").label() == "IS 1200 (Part 2) : 2025, cl. 5.1.8"


def test_failed_check_caps_confidence():
    c = cf.combine([cf.Factor(name="a", score=1.0, weight=5), cf.Factor(name="b", score=0.3, cap=0.6)])
    assert c["score"] == 0.6 and c["level"] == "low" and c["capped_by"] == ["b"]


def test_answer_confidence_penalises_ungrounded_numbers():
    good = cf.answer_confidence({"numbers_in_answer": 2, "grounded": 2, "ungrounded_numbers": []},
                                [{"verified": True}])
    bad = cf.answer_confidence({"numbers_in_answer": 2, "grounded": 0, "ungrounded_numbers": ["450", "0.4"]}, [])
    assert good["score"] == 1.0 and bad["level"] == "low"


def test_mix_output_has_grade_versions_and_confidence():
    o = design_mix(MixDesignInput(grade="M30", w_c_ratio=0.42, admixture=WR)).outputs
    assert o["mix_grade"] == "M30"
    assert o["code_versions"] == ["IS 10262 : 2019", "IS 456 : 2000 (incl. Amd 1-3)"]
    assert o["all_checks_passed"] and o["confidence"]["level"] == "high"
    o = design_mix(MixDesignInput(grade="M30", w_c_ratio=0.42)).outputs          # cement > 450 kg/m3
    assert not o["all_checks_passed"] and o["confidence"]["score"] <= 0.6


def test_concrete_mix_report_per_grade_with_wastage():
    r = concrete_mix_report(ConcreteMixRequest(
        mix=MixDesignInput(grade="M30", w_c_ratio=0.42, admixture=WR),
        elements=[{"id": "C1", "type": "column", "length_m": 3, "width_m": 0.3, "depth_m": 0.45, "count": 4,
                   "grade": "M30", "steel_kg_per_m3": 160},
                  {"id": "S1", "type": "slab", "length_m": 6, "width_m": 4.5, "depth_m": 0.125, "grade": "M25",
                   "steel_kg_per_m3": 90, "exposure": "mild"}],
        wastage_percent={"cement": 2.0}))
    m30, m25 = r["grades"]
    assert (m30["grade"], m25["grade"]) == ("M30", "M25")
    assert m30["w_c_ratio"] == 0.42 and m25["w_c_ratio"] != 0.42           # w/c input applies to M30 only
    assert m30["per_m3_with_wastage"]["cement_kg"] == round(m30["per_m3_field"]["cement_kg"] * 1.02, 1)
    assert m30["concrete_m3"] == 1.62 and r["concrete_m3"] == 1.62 + 3.38
    assert r["reinforcement"]["net_kg"] == round(1.62 * 160 + 3.38 * 90, 1)
    assert r["materials_to_procure"]["cement_bags"] == round(
        m30["materials_to_procure"]["cement_bags"] + m25["materials_to_procure"]["cement_bags"], 2)
    assert "IS 1200 (Part 2) : 2025" in r["code_versions"]
    assert any(c["ref"].startswith("IS 456 : 2000") for c in r["citations"])
    assert not r["wastage"]["approved"]                                    # overridden -> not the approved set
    assert {f["name"] for f in r["confidence"]["factors"]} >= {"mix design", "reinforcement quantities",
                                                              "wastage factors"}


@needs_corpus
def test_estimate_lines_mapped_with_confidence(monkeypatch):
    from concrete_rag.estimate import boq
    monkeypatch.setattr(boq.docstore, "save_estimate", lambda *a, **k: None)
    e = boq.build_estimate(boq.EstimateRequest(elements=[
        {"id": "S1", "type": "slab", "length_m": 6, "width_m": 4.5, "depth_m": 0.125, "grade": "M25",
         "steel_kg_per_m3": 90}]))
    assert {l["item_code"] for l in e["boq"]} == {"5.33.2.1", "5.9.3", "5.22A.6"}
    assert all(l["matched_by"] == "mapping" for l in e["boq"])
    assert e["confidence"]["score"] > 0.8
    assert e["sor_edition"].startswith("CPWD-DSR 2023 (w.e.f. 16.10.2023")
