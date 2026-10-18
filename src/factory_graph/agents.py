"""Bounded investigation tools. The model cannot submit arbitrary Cypher or act."""

import json
import os

import httpx

from .store import CANDIDATES, IMPACT, MATERIALS

TOOLS = {
    "trace_impact": IMPACT,
    "check_capacity": CANDIDATES,
    "check_materials": MATERIALS,
    "recall_incidents": "MATCH (m:Memory) WHERE m.machine=$machine "
    "RETURN m.summary,m.source ORDER BY m.created DESC LIMIT 10",
}


def investigate(store, machine, day, until):
    selected = ["trace_impact", "recall_incidents"]
    provider = "explicit investigation policy"
    model = os.getenv("OLLAMA_MODEL")
    if model:
        try:
            response = httpx.post(
                os.getenv("OLLAMA_URL", "http://localhost:11434") + "/api/chat",
                json={
                    "model": model,
                    "stream": False,
                    "format": "json",
                    "messages": [
                        {
                            "role": "system",
                            "content": "Choose investigation tools. Return JSON "
                            "with tools: an array drawn only from " + ", ".join(TOOLS),
                        },
                        {"role": "user", "content": f"Machine {machine} outage days {day}-{until}"},
                    ],
                },
                timeout=30,
            )
            response.raise_for_status()
            names = json.loads(response.json()["message"]["content"])["tools"]
            if not isinstance(names, list) or not names or any(n not in TOOLS for n in names):
                raise ValueError("Invalid tool selection")
            selected = list(dict.fromkeys(names))
            provider = f"Ollama / {model}"
        except (httpx.HTTPError, ValueError, KeyError, TypeError):
            provider += " (model unavailable or invalid)"
    trace = []
    for name in selected:
        rows = store.q(TOOLS[name], {"machine": machine, "day": day, "until": until})
        trace.append(
            {
                "agent": "investigator",
                "tool": name,
                "provider": provider,
                "rows": len(rows),
                "evidence": rows if name != "check_capacity" else rows[:20],
            }
        )
    return trace
