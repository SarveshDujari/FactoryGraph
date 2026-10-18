import json

import httpx
import pytest

from factory_graph.briefing import explain


@pytest.fixture
def proposal():
    return {
        "hash": "a" * 64,
        "revision": 4,
        "event": {"machine": "M1", "start_hour": 8, "end_hour": 10},
        "strategy": "standard",
        "assignments": [{"job": "J1", "machine": "M2"}],
        "financial": {"total_paise": 375000},
        "policy": {"source": "Synthetic demo"},
    }


def test_briefing_uses_server_evidence_without_mutating_plan(monkeypatch, proposal):
    before = json.dumps(proposal, sort_keys=True)

    def respond(url, **kwargs):
        assert url.endswith("/api/chat")
        assert kwargs["json"]["model"] == "gemma3:4b"
        assert "tools" not in kwargs["json"]
        evidence = json.loads(kwargs["json"]["messages"][1]["content"])
        assert "lower estimated cost" in evidence["facts"]["summary"]["comparison"]
        assert "financial" not in evidence and "baseline" not in evidence
        return httpx.Response(
            200,
            request=httpx.Request("POST", url),
            json={
                "message": {
                    "content": json.dumps(
                        {
                            "summary_ids": ["comparison", "schedule"],
                            "tradeoff_ids": ["estimate"],
                            "check_ids": ["approval", "rates"],
                        }
                    )
                }
            },
        )

    monkeypatch.setenv("OLLAMA_BRIEFING_MODEL", "gemma3:4b")
    monkeypatch.setattr(httpx, "post", respond)
    result = explain(proposal, {"total_paise": 400000})
    assert result["provider"] == "ollama" and result["hash"] == proposal["hash"]
    assert json.dumps(proposal, sort_keys=True) == before


@pytest.mark.parametrize(
    "content",
    [
        "not json",
        '{"summary_ids":["invented"],"tradeoff_ids":["estimate"],"check_ids":["approval"]}',
        '{"summary_ids":["comparison"],"tradeoff_ids":["estimate"],"check_ids":["rates"]}',
        '{"summary":"Missing fields"}',
        '{"summary":"x","tradeoffs":[],"checks":[],"execute":true}',
        '{"summary":"The cost is 400000 paise","tradeoffs":[],"checks":[]}',
    ],
)
def test_invalid_model_response_is_explicit_fallback(monkeypatch, proposal, content):
    monkeypatch.setattr(
        httpx,
        "post",
        lambda url, **kwargs: httpx.Response(
            200, request=httpx.Request("POST", url), json={"message": {"content": content}}
        ),
    )
    result = explain(proposal, None)
    assert result["status"] == "fallback" and result["provider"] == "deterministic"


def test_unavailable_ollama_does_not_break_recovery(monkeypatch, proposal):
    def unavailable(*args, **kwargs):
        raise httpx.ConnectError("offline")

    monkeypatch.setattr(httpx, "post", unavailable)
    assert explain(proposal, None)["status"] == "fallback"
