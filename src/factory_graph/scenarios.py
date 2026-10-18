"""Independent, synthetic scenario graphs; never resets an existing shop."""

SCENARIOS = {
    "default": "Machine outage",
    "cascade": "Cascading customer dependency",
    "scarce_operator": "Reduced operator shift",
    "supplier_delay": "Remembered supplier delay",
    "impossible": "No capacity in planning horizon",
    "repeat": "Repeated disruption and retained memory",
}


def configure(store, name):
    if name == "cascade":
        store.q("MATCH (a:Job {id:'J3'}),(b:Job {id:'J2'}) CREATE (a)-[:DEPENDS_ON]->(b)")
    elif name == "scarce_operator":
        store.q("MATCH (o:Operator {id:'O1'})-[:WORKS_SHIFT]->(s:Shift) SET s.hours=6")
    elif name == "supplier_delay":
        store.remember(
            "semantic",
            "Synthetic supplier observation",
            supplier="S1",
            observed_lead=2,
            summary="Previous delivery took two days; not a guarantee",
        )
    elif name == "impossible":
        store.q("MATCH (s:Slot) SET s.capacity=0")
    elif name == "repeat":
        store.remember(
            "episodic",
            "Synthetic previous incident",
            machine="M1",
            summary="Previous Atlas outage required procurement and overtime review",
        )
        store.remember(
            "semantic",
            "Synthetic received delivery",
            supplier="S1",
            observed_lead=2,
            summary="Received delivery after two days",
        )
