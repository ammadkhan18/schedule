"""Free-tier limits: request fitting, 413/429 handling and fallback to other providers (all offline, mocked)."""
from __future__ import annotations

import json
from types import SimpleNamespace as NS

import pytest
from PIL import Image

import ai.groq_client as g
import ai.llm as llm
import config

GROQ_413 = ("Error code: 413 - {'error': {'message': 'Request too large for model `qwen/qwen3.8-27b` in organization `org_x` "
            "service tier `on_demand` on input tokens per minute (ITPM): Limit 7000, Requested 8278, please reduce your "
            "message size and try again.', 'type': 'tokens', 'code': 'rate_limit_exceeded'}}")


class Err(Exception):
    def __init__(self, code, msg):
        super().__init__(msg)
        self.status_code = code


def _resp(text="OK", tool_calls=None):
    return NS(choices=[NS(message=NS(content=text, tool_calls=tool_calls))])


def _page():
    return Image.new("RGB", (2480, 1754), "white")  # an A4/A3 drawing page rendered at ~150-200 dpi


def test_error_classification():
    e = Err(413, GROQ_413)
    assert llm.is_too_large(e) and llm.is_rate_limited(Err(429, "Rate limit reached ... Please try again in 4.25s"))
    assert llm.retry_after_seconds(Err(429, "Please try again in 4.25s")) == pytest.approx(4.25)
    assert llm.retry_after_seconds(Err(429, "Please try again in 6m11.52s")) == pytest.approx(371.52)
    assert "free AI limit" in llm.friendly_error(Err(429, "rate limit"))


def test_fit_images_keeps_request_under_budget():
    imgs, dim, note = llm.fit_images([_page()] * 3, prompt_tokens=1800, budget=5500)
    total = 1800 + sum(llm.image_tokens(*i.size) for i in imgs)
    assert total <= 5500 and imgs and dim < 1600 and note
    # the original 3 x 1600 px request would have been far over the Groq free limit
    assert 1800 + 3 * llm.image_tokens(*llm._scaled(_page(), 1600)) > 7000


def test_groq_413_is_prevented_and_recovered(monkeypatch):
    monkeypatch.setattr(config, "GROQ_RETRY_BASE_DELAY_S", 0)
    sent = []

    class Client:
        class chat:
            class completions:
                @staticmethod
                def create(**kw):
                    imgs = [c for c in kw["messages"][1]["content"] if c["type"] == "image_url"]
                    sent.append(sum(len(c["image_url"]["url"]) for c in imgs))
                    if len(sent) == 1:
                        raise Err(413, GROQ_413)
                    return _resp('{"ok": true}')

    monkeypatch.setattr(g, "get_client", lambda key: Client)
    out = g.call_vision_model("k", "system " * 200, "user " * 400, [_page()] * 3)
    assert out == '{"ok": true}' and len(sent) == 2 and sent[1] < sent[0]  # shrank and retried


def test_falls_back_to_gemini_when_groq_is_out_of_limit(monkeypatch):
    monkeypatch.setattr(config, "GROQ_RETRY_BASE_DELAY_S", 0)
    monkeypatch.setattr(config, "GROQ_MAX_RETRIES", 0)

    class Groq:
        class chat:
            class completions:
                @staticmethod
                def create(**kw):
                    raise Err(429, "Rate limit reached for model on tokens per day. Please try again in 6m11.52s")

    used = {}

    def fake_compat(spec, key):
        def create(**kw):
            used["model"], used["n_images"] = kw["model"], sum(1 for c in kw["messages"][1]["content"] if c["type"] == "image_url")
            return _resp('{"from": "gemini"}')
        return NS(chat=NS(completions=NS(create=create)))

    monkeypatch.setattr(g, "get_client", lambda key: Groq)
    monkeypatch.setattr(llm, "compat_client", fake_compat)
    out = g.call_vision_model("k", "s", "u", [_page()] * 3, keys=llm.LLMKeys(groq="k", gemini="G"))
    assert out == '{"from": "gemini"}' and used["model"].startswith("gemini") and used["n_images"] == 3


def test_gemini_only_and_no_keys(monkeypatch):
    monkeypatch.setattr(llm, "compat_client", lambda spec, key: NS(chat=NS(completions=NS(create=lambda **kw: _resp("G")))))
    assert g.call_text_model("", "s", "u", keys=llm.LLMKeys(gemini="G")) == "G"
    with pytest.raises(g.GroqClientError):
        g.call_text_model("", "s", "u", keys=llm.LLMKeys())


def test_openai_compat_client_parses_tool_calls(monkeypatch):
    import requests
    captured = {}

    def post(url, headers=None, data=None, timeout=None):
        captured["url"], captured["body"] = url, json.loads(data)
        body = {"choices": [{"message": {"role": "assistant", "content": None, "tool_calls": [
            {"id": "c1", "type": "function", "function": {"name": "explain", "arguments": "{\"what\": \"steel\"}"}}]}}]}
        return NS(status_code=200, json=lambda: body, headers={}, text="")

    monkeypatch.setattr(requests, "post", post)
    c = llm.OpenAICompatClient("https://example.test/v1", "KEY")
    r = c.chat.completions.create(model="m", messages=[], reasoning_effort="none", tools=[])
    assert captured["url"] == "https://example.test/v1/chat/completions" and "reasoning_effort" not in captured["body"]
    tc = r.choices[0].message.tool_calls[0]
    assert tc.function.name == "explain" and json.loads(tc.function.arguments) == {"what": "steel"}

    monkeypatch.setattr(requests, "post", lambda *a, **k: NS(status_code=429, text="quota exceeded", headers={"retry-after": "3"},
                                                             json=lambda: {}))
    with pytest.raises(Exception) as ei:
        c.chat.completions.create(model="m", messages=[])
    assert llm.is_rate_limited(ei.value) and llm.retry_after_seconds(ei.value) == 3


def test_copilot_switches_provider_on_limit(monkeypatch):
    from copilot import agent
    from copilot.state import ProjectState
    from detailed_mto import build_project
    from detailed_mto.edits import openings_to_rows, rooms_to_rows
    from engineering import plot_templates
    from knowledge import load_knowledge_base
    from models.schemas import ProjectInputs

    pi = ProjectInputs(project_name="x", plot_marla=5)
    params = plot_templates.build_template_params(pi)
    p = build_project(pi, params, load_knowledge_base())
    state = ProjectState(pi, params, rooms_to_rows(p), openings_to_rows(p), {}, p.options, None, None, None, "none")

    class Groq:
        class chat:
            class completions:
                @staticmethod
                def create(**kw):
                    raise Err(413, GROQ_413)

    monkeypatch.setattr(g, "get_client", lambda key: Groq)
    monkeypatch.setattr(llm, "compat_client",
                        lambda spec, key: NS(chat=NS(completions=NS(create=lambda **kw: _resp("Answer from Gemini")))))
    reply = agent.run_agent("k", state, [], "hello", keys=llm.LLMKeys(groq="k", gemini="G"))
    assert reply.text == "Answer from Gemini" and not reply.error
    reply = agent.run_agent("k", state, [], "hello", keys=llm.LLMKeys(groq="k"))
    assert reply.error and "limit" in reply.error
