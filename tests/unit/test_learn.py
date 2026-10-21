import pytest
import yaml
from sqlalchemy import create_engine

from concrete_rag.learn import calibrate, data, state
from concrete_rag.retrieval.hybrid import fuse
from concrete_rag.store import docstore


@pytest.fixture
def mem_db(monkeypatch):
    eng = create_engine("sqlite://", future=True)
    docstore.md.create_all(eng)
    monkeypatch.setattr(docstore, "engine", lambda: eng)
    return eng


@pytest.fixture
def tmp_state(tmp_path, monkeypatch):
    monkeypatch.setattr(state, "_path", lambda: tmp_path / "active.json")
    monkeypatch.setattr(state, "_cache", {"mtime": None, "data": {}})


def test_feedback_roundtrip_and_examples(mem_db):
    qid = docstore.log_query("retrieve", "lap length in tension", None, ["a", "b", "c", "d"])
    docstore.add_feedback(qid, "chunk", 1, chunk_id="c", key_name="eng1")
    docstore.add_feedback(qid, "chunk", -1, chunk_id="d", key_name="eng1")
    docstore.add_feedback(qid, "chunk", 1, chunk_id="d", key_name="eng1")      # re-click replaces the 👎
    ex = data.retrieval_examples()
    assert len(ex) == 1
    assert ex[0]["pos"] == {"c", "d"}
    assert ex[0]["neg"] == {"a", "b"}                                         # skip-above: shown above a 👍
    fid = docstore.list_feedback()[0]["id"]
    docstore.set_feedback_status(fid, "discarded", "admin")
    assert docstore.feedback_counts()["chunk"] == {"new": 1, "discarded": 1}


def test_discarded_feedback_is_ignored(mem_db):
    qid = docstore.log_query("retrieve", "q", None, ["a", "b"])
    fid = docstore.add_feedback(qid, "chunk", 1, chunk_id="b")
    docstore.set_feedback_status(fid, "discarded", "admin")
    assert data.retrieval_examples() == []


def test_split_is_deterministic_by_query():
    ex = [{"query": f"question {i}"} for i in range(200)]
    a, b = data.split(ex)
    a2, b2 = data.split(list(reversed(ex)))
    assert {e["query"] for e in b} == {e["query"] for e in b2}
    assert 0.1 < len(b) / 200 < 0.3


def test_weighted_fusion_changes_order():
    p = lambda cid: {"chunk_id": cid, "chunk_type": "clause", "text": cid}  # noqa: E731
    lists = [("dense", [(p("x"), 0), (p("y"), 0)]), ("bm25", [(p("y"), 0), (p("x"), 0)])]
    assert [c["chunk_id"] for c, _, _ in fuse(lists, {"dense": 2.0, "bm25": 1.0})] == ["x", "y"]
    assert [c["chunk_id"] for c, _, _ in fuse(lists, {"dense": 0.5, "bm25": 1.0})] == ["y", "x"]


def test_promote_and_rollback(tmp_state):
    assert state.fusion_weights() == state.DEFAULT_WEIGHTS
    state.promote("fusion", {"version": state.next_version("fusion"), "weights": {"dense": 1.5, "bm25": 1.0}})
    state.promote("fusion", {"version": state.next_version("fusion"), "weights": {"dense": 2.0, "bm25": 1.0}})
    assert state.fusion_weights()["dense"] == 2.0
    with state.override(fusion={"dense": 0.5, "bm25": 1.0}):
        assert state.fusion_weights()["dense"] == 0.5
    assert state.rollback("fusion")["version"] == 1
    assert state.fusion_weights()["dense"] == 1.5
    state.rollback("fusion")
    assert state.fusion_weights() == state.DEFAULT_WEIGHTS
    assert state.next_version("fusion") == 3                                  # versions are never reused


def test_calibration_blends_with_default_and_needs_approval(tmp_path, monkeypatch):
    monkeypatch.setattr(calibrate, "path", lambda: tmp_path / "learned.yaml")
    csv = tmp_path / "actual.csv"
    csv.write_text("project,item_code,description,unit,est_qty,est_amount,actual_qty,actual_amount,element_type,concrete_m3\n"
                   "P1,5.22A.6,steel columns,kg,4608,1,4000,1,column,25\n"         # observed 160 kg/m3
                   "P2,5.22.6,steel footings,kg,800,1,900,1,footing,10\n"          # observed 90 kg/m3
                   "P2,5.33.2.1,concrete,m3,100,1,104,1,,\n")
    r = calibrate.calibrate(csv)
    col = r["steel_kg_per_m3"]["column"]
    assert col["observed"] == 160.0 and col["projects"] == 1
    assert col["value"] == round((3 * 180 + 160) / 4, 1)                       # default 180 blended, 175.0
    assert calibrate.approved_steel_density("column") is None                 # proposed only
    calibrate.approve("tester")
    assert calibrate.approved_steel_density("column")["value"] == 175.0
    text = (tmp_path / "learned.yaml").read_text()
    assert text.startswith("# Written by") and yaml.safe_load(text)["approved_by"] == "tester"
