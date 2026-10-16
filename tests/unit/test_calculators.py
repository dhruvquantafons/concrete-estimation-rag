import pytest

from concrete_rag.calc.mix_design import Admixture, MixDesignInput, design_mix
from concrete_rag.calc.quantities import Element, Opening, rnd, takeoff
from concrete_rag.calc.rcc import BeamInput, ColumnInput, design_beam, design_column_axial
from concrete_rag.calc.steel import BarMark, bbs, development_length, unit_weight_kg_m


def annex_a():
    return design_mix(MixDesignInput(
        grade="M40", exposure="severe", max_aggregate_size_mm=20, slump_mm=75, cement_type="PPC", sg_cement=2.88,
        sg_coarse=2.74, sg_fine=2.65, fine_aggregate_zone="II", w_c_ratio=0.36,
        admixture=Admixture(type="superplasticizer", dosage_percent=1.0, water_reduction_percent=23,
                            specific_gravity=1.145)))


def test_is10262_annex_a_reproduced():
    o = annex_a().outputs
    s = o["per_m3_ssd"]
    assert s["cement_kg"] == 412 and s["water_kg"] == 148
    assert abs(s["fine_aggregate_kg"] - 648) <= 1.5
    assert abs(s["coarse_aggregate_kg"] - 1234) <= 1.5
    assert o["target_strength_n_mm2"] == 48.25
    assert o["ca_volume_fraction"] == 0.648
    f = o["per_m3_field"]
    assert abs(f["water_to_add_kg"] - 160) <= 1.0 and abs(f["coarse_aggregate_kg"] - 1228) <= 1.0


def test_mix_requires_wc_without_fig1_curve():
    with pytest.raises(ValueError, match="w_c_ratio is required"):
        design_mix(MixDesignInput(grade="M30"))


def test_wc_capped_by_durability():
    r = design_mix(MixDesignInput(grade="M30", exposure="severe", w_c_ratio=0.55))
    assert r.outputs["w_c_ratio"] <= 0.45
    assert any("exceeds durability limit" in w for w in r.warnings)


def test_development_length_m25_fe500_16mm():
    ld, _ = development_length(16, "M25", 500)
    assert round(ld) == 777          # 16 x 0.87 x 500 / (4 x 1.4 x 1.6)


def test_bar_weight_and_bbs():
    assert abs(unit_weight_kg_m(16) - 1.580) < 0.001
    out = bbs([BarMark(mark="a", member="B1", dia_mm=16, shape="straight", a_mm=5000, nos=4, members=2)])
    assert out["total_weight_kg"] == round(40 * 256 / 162, 2)


def test_beam_singly_reinforced():
    r = design_beam(BeamInput(b_mm=230, D_mm=450, grade="M25", fy=500, Mu_kNm=90, Vu_kN=80))
    assert r.outputs["Asc_required_mm2"] == 0
    assert 550 < r.outputs["Ast_required_mm2"] < 600       # hand calc ~ 569 mm2
    assert r.ok


def test_column_flags_min_eccentricity():
    r = design_column_axial(ColumnInput(b_mm=300, D_mm=450, Pu_kN=1800))
    assert not r.ok   # e_min (20 mm) > 0.05 x 300 -> cl. 39.3 formula not applicable


def test_is1200_rounding_and_openings():
    assert rnd(0.127, "0.005") == 0.125
    q = takeoff([Element(id="S", type="slab", length_m=6, width_m=4.5, depth_m=0.127,
                         openings=[Opening(width_m=0.3, height_m=0.3), Opening(width_m=1, height_m=1)])])
    row = q["rows"][0]
    assert row["concrete_m3"] == round(6 * 4.5 * 0.125 - 1 * 0.125, 2)   # small opening not deducted
