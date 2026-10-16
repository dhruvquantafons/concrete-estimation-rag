"""Run after `crag ingest` + `crag index`."""
import uuid

from tests.conftest import needs_corpus


@needs_corpus
def test_key_dsr_items_parsed_with_confirmed_rates():
    from concrete_rag.store import docstore
    for code, unit in [("5.33.1.1", "m3"), ("5.9.3", "m2"), ("5.22.6", "kg"), ("4.1.8", "m3")]:
        it = docstore.get_sor_item(code)
        assert it and it["unit"] == unit and it["rate"] and not it["needs_review"], code


@needs_corpus
def test_is456_tables_present():
    from concrete_rag.store import docstore
    for t in ("5", "16", "19", "20"):
        assert docstore.find_table("IS 456", t), t


@needs_corpus
def test_retrieval_finds_table_5():
    from concrete_rag.retrieval.hybrid import retrieve
    p = retrieve("minimum cement content maximum water cement ratio severe exposure", top_k=5, use_rerank=False)
    assert any(c.metadata.get("table_no") == "5" and c.metadata["code_no"] == "IS 456" for c in p.chunks)


@needs_corpus
def test_api_auth_and_estimate():
    from fastapi.testclient import TestClient

    from concrete_rag.api.auth import create_key
    from concrete_rag.api.main import app
    from concrete_rag.store.docstore import revoke_api_key
    name = "pytest-" + uuid.uuid4().hex[:6]
    key = create_key(name, ["estimate"])
    c = TestClient(app)
    assert c.get("/v1/rates/5.9.3").status_code == 401
    assert c.post("/v1/mix-design", json={"grade": "M25", "w_c_ratio": 0.45}, headers={"X-API-Key": key}).status_code == 403
    r = c.post("/v1/estimate", headers={"X-API-Key": key},
               json={"elements": [{"id": "S1", "type": "slab", "length_m": 5, "width_m": 4, "depth_m": 0.15}]})
    assert r.status_code == 200 and r.json()["summary"]["grand_total"] > 0
    revoke_api_key(name)
