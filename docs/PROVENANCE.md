# Provenance and original contribution

Reference: https://github.com/TheAmericanMaker/production-orchestrator
Inspected commit: `1067e5aba9b61ea11fc3cb15691f664b6f14648e`.
The reference repository is not bundled with this project.

Architectural ideas retained: deterministic constraint validation, canonical
proposal hashes, explicit human approval, persistence across process restart,
and unsent communication drafts. Upstream's small shop domain informed the demo.

New implementation: FalkorDB entity model and Cypher context queries; machine
failure and dependent commitment analysis; graph-derived resource candidates;
bounded capacity-reserving recovery search; operator, precedence and material
constraints; overtime alternative; exact-plan graph writes; decision memory and
graph-based procurement handoffs; LangGraph interrupts and SQLite checkpoints;
FastAPI application, local browser interface, packaging and integration tests.

No upstream Python application file, UI, deployment manifest, video or test
evidence was copied. LICENSE preserves upstream's Apache-2.0 text, and NOTICE
credits James Sesler and contributors and distinguishes actual dependencies.
The original application source here is distributed under Apache-2.0.

This project was built with substantial Codex AI coding assistance on October 17,
2026. The participant must review, understand and verify the implementation and
disclose that assistance in the submission. Do not claim unaided authorship or
assert eligibility on behalf of the organisers. Source publication does not
constitute a public deployment or a hackathon submission.
