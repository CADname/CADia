from standalonecad_web.providers.anthropic_api import AnthropicAPIProvider
from standalonecad_web.providers.gemini_api import GeminiAPIProvider


def test_anthropic_models_and_json(monkeypatch):
    p = AnthropicAPIProvider("k", default_model="claude-test")
    def fake(path, **kwargs):
        if path == "/v1/models":
            return {"data": [{"id": "claude-test", "display_name": "Claude Test"}]}
        return {"content": [{"type": "text", "text": '{"calls":[{"tool":"cad_create_box","arguments":{"length":10,"width":10,"height":10}}],"note":"ok"}'}]}
    monkeypatch.setattr(p, "_request", fake)
    assert p.list_models()[0]["model"] == "claude-test"
    out = p.complete_json("x", output_schema={"type": "object"})
    assert out["calls"][0]["tool"] == "cad_create_box"


def test_gemini_models_and_json(monkeypatch):
    p = GeminiAPIProvider("k", default_model="gemini-test")
    def fake(path, **kwargs):
        if path == "/models":
            return {"models": [{"name": "models/gemini-test", "displayName": "Gemini Test", "supportedGenerationMethods": ["generateContent"]}]}
        return {"candidates": [{"content": {"parts": [{"text": '{"calls":[{"tool":"cad_create_box","arguments":{"length":10,"width":10,"height":10}}],"note":"ok"}'}]}}]}
    monkeypatch.setattr(p, "_request", fake)
    assert p.list_models()[0]["model"] == "gemini-test"
    out = p.complete_json("x", output_schema={"type": "object"})
    assert out["calls"][0]["tool"] == "cad_create_box"
