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


def test_mix_wc_from_digitized_fig1():
    # IS 10262 Annex A: target 48.25 N/mm2 on curve 2 (OPC 43); the drawn curve gives 0.35 (the example rounds to 0.36)
    r = design_mix(MixDesignInput(grade="M40", exposure="severe", cement_type="OPC43"))
    wc = next(s.value for s in r.steps if s.name == "free w/c from Fig. 1")
    assert abs(wc - 0.35) <= 0.005
    r = design_mix(MixDesignInput(grade="M60", cement_type="OPC33"))          # 68.25 > curve 1 maximum (60)
    assert any("outside IS 10262 Fig. 1" in w for w in r.warnings)


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


def test_footing_rectangular_column_uses_longer_cantilever():
    from concrete_rag.calc.rcc import FootingInput, design_footing_square
    r = design_footing_square(FootingInput(column_b_mm=230, column_D_mm=600, service_load_kN=900, sbc_kN_m2=150))
    mu = next(s for s in r.steps if s.name.startswith("Mu at column face"))
    qu = 1.5 * 900 / 2.6 ** 2
    assert abs(mu.value - qu * 2.6 * ((2.6 - 0.23) / 2) ** 2 / 2) < 0.1     # cantilever from the 230 mm side


def test_deflection_uses_fig4_modification_factor():
    from concrete_rag.calc.rcc import span_depth_allowed
    allowed, _ = span_depth_allowed("simply_supported", 4.0, 500, 600, 600, 1000, 130)   # fs 290, pt 0.46 %
    assert 20 < allowed < 22.5          # Fig. 4 MF ~1.1 for Fe500 - not the old 2.0 cap (40)
    allowed_c, _ = span_depth_allowed("simply_supported", 4.0, 500, 600, 600, 1000, 130, asc_prov=650)
    assert allowed_c > allowed          # Fig. 5 compression steel increases the limit


def test_boq_steel_item_by_level():
    from concrete_rag.estimate.boq import _cfg
    m = _cfg("boq_mapping")["reinforcement"]
    assert m["below_plinth"] == "5.22.6" and m["above_plinth"] == "5.22A.6"


def test_rcc_results_are_labelled_design_aid():
    from concrete_rag.calc.rcc import DESIGN_AID, FootingInput, SlabInput, design_footing_square, design_slab_one_way
    for r in (design_beam(BeamInput(b_mm=230, D_mm=450, grade="M25", fy=500, Mu_kNm=90, Vu_kN=80)),
              design_slab_one_way(SlabInput(span_m=3.5)),
              design_column_axial(ColumnInput(b_mm=300, D_mm=450, Pu_kN=1500)),
              design_footing_square(FootingInput(column_b_mm=300, column_D_mm=450, service_load_kN=1200, sbc_kN_m2=200))):
        assert r.outputs["label"] == "Design aid only"
        assert r.warnings[0] == DESIGN_AID


def test_ai_check_is_not_human_verification():
    from concrete_rag.calc.code_tables import signed_status
    assert signed_status("claude-vision (checked against scanned page)") == "ai_checked"
    assert signed_status("R. Sharma") == "verified"
    assert signed_status(None) is None


def test_field_moisture_adjustment_annex_a_and_b():
    from concrete_rag.calc.mix_design import field_mass
    # Annex B-11 (wet): CA 1069 kg SSD, free moisture 1.5 % -> 1085; FA 719 kg, 4.0 % -> 748; water 155 - 16 - 29 = 110
    ca, fa = field_mass(1069, 0.5, 1.5), field_mass(719, 1.0, 4.0)
    assert round(ca) == 1085 and round(fa) == 748
    assert round(155 + (1069 - ca) + (719 - fa)) == 110
    # Annex A-10 (dry): CA 1234 kg SSD, absorption 0.5 % -> 1228; extra water 6 kg
    assert round(field_mass(1234, 0.5, 0)) == 1228


def test_hysd_compression_steel_stress_follows_fig_23a():
    from concrete_rag.calc.rcc import steel_stress
    # SP 16 Table A, Fe 415: 0.95 fyd at strain 0.00241, 0.975 fyd at 0.00276; Fe 500: 0.90 fyd at 0.00226
    assert abs(steel_stress(0.00241, 415) - 342.8) < 0.5 and abs(steel_stress(0.00276, 415) - 351.8) < 0.5
    assert abs(steel_stress(0.00226, 500) - 391.3) < 0.5
    assert steel_stress(0.001, 500) == 200.0 and steel_stress(0.01, 500) == 0.87 * 500   # elastic / yield plateau


def test_doubly_reinforced_beam_uses_fig_23a_stress():
    r = design_beam(BeamInput(b_mm=230, D_mm=450, grade="M20", fy=500, Mu_kNm=180, Vu_kN=80))
    fsc = next(s.value for s in r.steps if s.name.startswith("stress in compression steel"))
    assert fsc < 0.87 * 500                      # below yield at the compression steel strain (Fig. 23A)
    assert r.outputs["Asc_required_mm2"] > 0


def test_continuous_slab_uses_table_12_and_13():
    from concrete_rag.calc.rcc import SlabInput, design_slab_one_way
    r = design_slab_one_way(SlabInput(span_m=3.5, D_mm=150, support="continuous", live_load_kN_m2=3.0,
                                      floor_finish_kN_m2=1.0))
    wd, wl = 1.5 * (25 * 0.15 + 1.0), 1.5 * 3.0
    assert abs(r.outputs["Mu_kNm_per_m"] - round((wd / 10 + wl / 9) * 3.5 ** 2, 3)) < 1e-6
    vu = next(s.value for s in r.steps if s.name.startswith("Vu per m width"))
    assert abs(vu - round(0.6 * (wd + wl) * 3.5, 3)) < 1e-6


def test_count_units_convert():
    from concrete_rag.normalize.units import convert_quantity
    assert convert_quantity(25, "each", "10_nos") == 2.5 and convert_quantity(3, "1000_nos", "each") == 3000


def test_slab_without_depth_is_deepened_until_deflection_passes():
    from concrete_rag.calc.rcc import SlabInput, design_slab_one_way
    r = design_slab_one_way(SlabInput(span_m=3.5))
    assert r.ok and r.outputs["D_mm"] == 160        # trial 150 mm fails span/d with the actual Fig. 4 factor
