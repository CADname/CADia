from __future__ import annotations

import json

from standalonecad_web.providers.openai_api import OpenAIAPIProvider
from standalonecad_web.web_agent import PLAN_OUTPUT_SCHEMA


def test_operator_api_fallback_uses_responses_structured_output():
    captured = {}

    def transport(payload):
        captured.update(payload)
        plan = {"calls": [{"tool": "cad_create_box", "arguments": {"length_mm": 10}}], "note": "fallback"}
        return {"output": [{"content": [{"type": "output_text", "text": json.dumps(plan)}]}]}

    provider = OpenAIAPIProvider(api_key="test", default_model="api-model", transport=transport)
    result = provider.complete_json(
        "make a box",
        output_schema=PLAN_OUTPUT_SCHEMA,
        effort="medium",
    )

    assert result["note"] == "fallback"
    assert captured["model"] == "api-model"
    assert captured["store"] is False
    assert captured["reasoning"] == {"effort": "medium"}
    assert captured["text"]["format"]["type"] == "json_schema"
    assert captured["text"]["format"]["strict"] is True
