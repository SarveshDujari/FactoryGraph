import os
import subprocess
import sys
import uuid

import pytest

from factory_graph.seed import seed
from factory_graph.store import Store
from factory_graph.workflow import decide, procurement_handoff, run

pytestmark = pytest.mark.integration


@pytest.fixture
def shop(tmp_path):
    if os.getenv("RUN_INTEGRATION") != "1":
        pytest.skip("Set RUN_INTEGRATION=1 to use real FalkorDB")
    s = Store("test_" + uuid.uuid4().hex)
    seed(s)
    yield s, str(tmp_path / "checkpoints.sqlite")
    s.graph.delete()


def test_multihop_impact_and_graph_candidates(shop):
    s, _ = shop
    c = s.context("M1", 0, 0)
    assert ["J1", "J2", "K1", "City Hospital"] in c["impact"]
    assert c["candidates"]


def test_reject_preserves_schedule(shop):
    s, path = shop
    before = s.board()
    result = run(s, "reject", initial={"machine": "M1", "day": 0, "until": 0}, checkpoint=path)
    h = result["plans"][0]["hash"]
    result = run(s, "reject", decision={"hash": h, "approve": False}, checkpoint=path)
    assert result["result"]["status"] == "rejected"
    assert s.board()["jobs"] == before["jobs"]
    assert s.board()["revision"] == 0


def test_exact_approval_resumes_in_fresh_process(shop):
    s, path = shop
    result = run(s, "restart", initial={"machine": "M1", "day": 0, "until": 0}, checkpoint=path)
    h = result["plans"][0]["hash"]
    script = (
        "from factory_graph.store import Store; from factory_graph.workflow import run; "
        f"print(run(Store({s.name!r}),'restart',decision={{'hash':{h!r},'approve':True}},"
        f"checkpoint={path!r})['result'])"
    )
    proc = subprocess.run(
        [sys.executable, "-c", script], capture_output=True, text=True, check=False
    )
    assert proc.returncode == 0, proc.stderr
    assert s.board()["revision"] == 1
    assert s.board()["tasks"][0]["status"] == "drafted"
    assert procurement_handoff(s) == []
    assert decide(s, h, True)["replayed"]
    assert s.board()["revision"] == 1


def test_stale_plan_cannot_apply(shop):
    s, path = shop
    result = run(s, "stale", initial={"machine": "M1", "day": 0, "until": 0}, checkpoint=path)
    s.q("MATCH (s:Shop) SET s.revision=s.revision+1")
    with pytest.raises(ValueError, match="Shop changed"):
        decide(s, result["plans"][0]["hash"], True)


def test_wrong_hash_and_tampering_fail_closed(shop):
    s, path = shop
    result = run(s, "wrong", initial={"machine": "M1", "day": 0, "until": 0}, checkpoint=path)
    with pytest.raises(ValueError, match="exact proposal"):
        run(s, "wrong", decision={"hash": "0" * 64, "approve": True}, checkpoint=path)
    h = result["plans"][0]["hash"]
    s.q("MATCH (p:Plan {id:$id}) SET p.payload=$payload", {"id": h, "payload": "{}"})
    with pytest.raises(ValueError, match="altered"):
        decide(s, h, True)
    assert s.board()["revision"] == 0


def test_empty_procurement_and_existing_outage_exclusion(shop):
    s, path = shop
    result = run(
        s, "no_materials", initial={"machine": "M2", "day": 0, "until": 0}, checkpoint=path
    )
    p = result["plans"][0]
    assert p["procurement"] == []
    decide(s, p["hash"], True)
    assert s.board()["revision"] == 1
    assert s.board()["tasks"] == []
    assert not any(r[1] == "M2" and r[2] == 0 for r in s.context("M1", 0, 0)["candidates"])


def test_rejection_does_not_poison_new_incident(shop):
    s, path = shop
    first = run(s, "first", initial={"machine": "M1", "day": 0, "until": 0}, checkpoint=path)
    decide(s, first["plans"][0]["hash"], False)
    second = run(s, "second", initial={"machine": "M1", "day": 0, "until": 0}, checkpoint=path)
    assert first["plans"][0]["hash"] != second["plans"][0]["hash"]
    decide(s, second["plans"][0]["hash"], True)
    assert s.props("Material")[0]["stock"] == 0
