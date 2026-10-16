from decimal import Decimal

from concrete_rag.normalize.currency import format_inr, parse_inr
from concrete_rag.normalize.item_codes import decode_sequence
from concrete_rag.normalize.units import convert, normalize_unit
from concrete_rag.parse.sor import _vote, norm_rate


def test_units():
    assert normalize_unit("cum") == "m3"
    assert normalize_unit("Sqm") == "m2"
    assert normalize_unit("Rmt") == "m"
    assert normalize_unit("qtl") == "quintal"
    assert normalize_unit("mi") == "ml"          # OCR fix
    assert abs(convert(1, "t", "kg") - 1000) < 1e-9


def test_currency():
    assert parse_inr("₹ 1,23,456.50") == Decimal("123456.50")
    assert parse_inr("2.5 lakh") == Decimal("250000.0")
    assert format_inr(12345678.9) == "₹ 1,23,45,678.90"


def test_rate_normalisation_and_vote():
    assert norm_rate("$340.85") == "8340.85"
    assert norm_rate("7654 85") == "7654.85"
    assert norm_rate("765485") == "7654.85"
    assert _vote(["7294.70", "7294.70", "7254.70"]) == ("7294.70", 2)
    assert _vote(["1205.85", "12205.85", "1205.00"])[0] == "12205.85"   # dropped-digit recovery


def test_dsr_code_decoding_real_ocr_sequences():
    raws = ["5.1", "5.12", "5.1.24", "543", "5.1.34", "5.2", "5.22", "5.224", "53", "53A", "54"]
    rated = [False, True, True, True, True, False, True, True, True, True, True]
    got = [c for c, _ in decode_sequence(raws, 5, rated)]
    assert got == ["5.1", "5.1.2", "5.1.2A", "5.1.3", "5.1.3A", "5.2", "5.2.2", "5.2.2A", "5.3", "5.3A", "5.4"]
    got = [c for c, _ in decode_sequence(["59", "594", "592", "593"], 5, [False, True, True, True], prev="5.8A")]
    assert got == ["5.9", "5.9.1", "5.9.2", "5.9.3"]
    got = [c for c, _ in decode_sequence(["5.22A1", "5.22A2"], 5, [True, True], prev="5.22.6")]
    assert got == ["5.22A.1", "5.22A.2"]
