"""Two incidents, real supplier feedback, and a fresh-process memory recall."""

import json
import subprocess
import sys
import tempfile
import uuid
from pathlib import Path

from .seed import seed
from .store import Store
from .workflow import decide, run


def main():
    store = Store("test_demo_" + uuid.uuid4().hex)
    try:
        seed(store)
        with tempfile.TemporaryDirectory() as directory:
            checkpoint = str(Path(directory) / "demo.sqlite")
            first = run(
                store,
                "first",
                initial={"machine": "M1", "day": 0, "until": 0},
                checkpoint=checkpoint,
            )
            decide(store, first["plans"][0]["hash"], True)
            task = store.board()["tasks"][0]
            store.feedback(task["id"], 2, received=True)
            # A subsequent synthetic order needs a new delivery from the same supplier.
            with store.lock():
                store.q(
                    "MATCH (m:Machine {id:'M1'}),(t:Material {id:'T1'}),(s:Shop) "
                    "CREATE (j:Job {id:'J5',name:'Follow-up uniforms',kind:'embroidery',"
                    "hours:2,due:3,priority:10,day:2,machine:'M1'}) "
                    "CREATE (j)-[:ASSIGNED_TO]->(m) "
                    "CREATE (j)-[:REQUIRES {quantity:2}]->(t) SET s.revision=s.revision+1"
                )
            code = (
                "import json; from factory_graph.store import Store; "
                "from factory_graph.workflow import run; "
                f"print(json.dumps(run(Store({store.name!r}), 'second', "
                "initial={'machine':'M1','day':2,'until':2}, "
                f"checkpoint={checkpoint!r})))"
            )
            child = subprocess.run(
                [sys.executable, "-c", code], capture_output=True, text=True, check=True
            )
            second = json.loads(child.stdout)
            plan = second["plans"][0]
            assert plan["procurement"][0]["arrival_day"] == 4
            assert any(m.get("observed_lead") == 2 for m in plan["memory_evidence"])
            report = {
                "first_plan": first["plans"][0],
                "received_day": 2,
                "second_process_plan": plan,
                "proof": "Declared lead is 1 day; remembered observed lead is 2. "
                "The new order requires arrival day 4, not day 3.",
            }
            Path("docs/two-incident-demo.json").write_text(
                json.dumps(report, indent=2), encoding="utf-8"
            )
            print(report["proof"])
    finally:
        store.graph.delete()


if __name__ == "__main__":
    main()
