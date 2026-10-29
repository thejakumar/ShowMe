"""The live API path, exercised against a fake OpenAI-compatible server (no network needed)."""

from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

import httpx
import pytest
from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "backend"))

from apprentice.config import Settings  # noqa: E402
from apprentice.muse import MuseClient, MuseError, extract_json  # noqa: E402
from apprentice.schemas import DetectionReply, StepCheckReply  # noqa: E402
from apprentice.trace import Tracer  # noqa: E402


def make_client(tmp_path, handler, **overrides) -> MuseClient:
    settings = Settings(api_key="test-key", mock=False, base_url="https://api.example/v1",
                        data_dir=tmp_path / "d", trace_dir=tmp_path / "t", **overrides)
    client = MuseClient(settings, Tracer(tmp_path / "t"))
    client._http = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    return client


def reply(content: str, status: int = 200, **extra) -> httpx.Response:
    body = {"id": "req_123", "model": "muse-spark-1.3", "choices": [{"message": {"role": "assistant", "content": content}}],
            "usage": {"prompt_tokens": 10, "completion_tokens": 5}, **extra}
    return httpx.Response(status, json=body)


def run(coro):
    return asyncio.run(coro)


def test_extract_json_variants():
    assert extract_json('```json\n{"a": 1}\n```') == {"a": 1}
    assert extract_json('Sure! {"a": {"b": "}"}} trailing') == {"a": {"b": "}"}}
    with pytest.raises(ValueError):
        extract_json("no json here")


def test_sends_images_and_parses(tmp_path):
    seen = {}

    def handler(request: httpx.Request):
        seen["body"] = json.loads(request.content)
        seen["auth"] = request.headers["authorization"]
        return reply('```json\n{"verdict": "pass", "confidence": 0.9, "box": [100, 100, 500, 600]}\n```')

    c = make_client(tmp_path, handler)
    out = run(c.json(role="spark", purpose="step_check", system="s", text="t", schema=StepCheckReply,
                     images=[Image.new("RGB", (3000, 2000))], effort="low"))
    assert out.verdict == "pass" and out.box == [100, 100, 500, 600]
    body = seen["body"]
    assert seen["auth"] == "Bearer test-key"
    assert body["model"] == "muse-spark-1.3"
    assert body["reasoning_effort"] == "low"
    parts = body["messages"][1]["content"]
    assert parts[1]["type"] == "image_url" and parts[1]["image_url"]["url"].startswith("data:image/jpeg;base64,")
    trace = (tmp_path / "t").glob("*.jsonl")
    lines = [json.loads(l) for f in trace for l in f.read_text().splitlines()]
    assert lines[-1]["request_id"] == "req_123" and lines[-1]["tag"] == "cloud"


def test_drops_rejected_params(tmp_path):
    calls = []

    def handler(request: httpx.Request):
        body = json.loads(request.content)
        calls.append(body)
        if "reasoning_effort" in body:
            return httpx.Response(400, json={"error": {"message": "Unknown parameter: reasoning_effort"}})
        return reply('{"detections": []}')

    c = make_client(tmp_path, handler)
    run(c.json(role="spark", purpose="x", system="s", text="t", schema=DetectionReply, effort="low"))
    assert len(calls) == 2 and "reasoning_effort" not in calls[1]
    run(c.json(role="spark", purpose="x", system="s", text="t", schema=DetectionReply, effort="low"))
    assert len(calls) == 3  # remembered: no second rejection


def test_repairs_bad_json_once(tmp_path):
    answers = iter(["I think it passed.", '{"verdict": "fail", "confidence": 0.8}'])

    def handler(request: httpx.Request):
        return reply(next(answers))

    c = make_client(tmp_path, handler)
    out = run(c.json(role="spark", purpose="step_check", system="s", text="t", schema=StepCheckReply))
    assert out.verdict == "fail"


def test_auth_error_is_clear(tmp_path):
    c = make_client(tmp_path, lambda r: httpx.Response(401, json={"error": "bad key"}))
    with pytest.raises(MuseError, match="rejected the key"):
        run(c.json(role="spark", purpose="x", system="s", text="t", schema=DetectionReply))


def test_retries_rate_limit(tmp_path, monkeypatch):
    statuses = iter([429, 200])

    async def no_sleep(_):
        return None

    monkeypatch.setattr(asyncio, "sleep", no_sleep)

    def handler(request):
        s = next(statuses)
        return httpx.Response(429, json={}) if s == 429 else reply('{"detections": []}')

    c = make_client(tmp_path, handler)
    out = run(c.json(role="spark", purpose="x", system="s", text="t", schema=DetectionReply))
    assert out.detections == []


def test_switches_to_messages_api(tmp_path):
    seen = []

    def handler(request: httpx.Request):
        seen.append(request.url.path)
        if request.url.path.endswith("/chat/completions"):
            return httpx.Response(404, json={"error": "Not Found"})
        body = json.loads(request.content)
        assert body["system"] == "s" and body["max_tokens"] > 0
        parts = body["messages"][0]["content"]
        assert parts[0]["type"] == "text"
        if len(parts) > 1:
            assert parts[1]["type"] == "image" and parts[1]["source"]["media_type"] == "image/jpeg"
        assert request.headers["anthropic-version"]
        return httpx.Response(200, json={"id": "msg_1", "model": "muse-wonder-1.0",
                                         "content": [{"type": "text", "text": '{"detections": []}'}]})

    c = make_client(tmp_path, handler)
    run(c.json(role="spark", purpose="x", system="s", text="t", schema=DetectionReply, images=[Image.new("RGB", (64, 64))]))
    run(c.json(role="spark", purpose="x", system="s", text="t", schema=DetectionReply))
    assert seen == ["/v1/chat/completions", "/v1/messages", "/v1/messages"]


def test_falls_back_to_wonder_when_spark_id_unknown(tmp_path):
    models = []

    def handler(request: httpx.Request):
        body = json.loads(request.content)
        models.append(body["model"])
        if body["model"] == "muse-spark-1.3":
            return httpx.Response(404, json={"error": {"message": "The model muse-spark-1.3 does not exist"}})
        return reply('{"detections": []}')

    c = make_client(tmp_path, handler)
    run(c.json(role="spark", purpose="x", system="s", text="t", schema=DetectionReply))
    run(c.json(role="spark", purpose="x", system="s", text="t", schema=DetectionReply))
    assert models == ["muse-spark-1.3", "muse-wonder-1.0", "muse-wonder-1.0"]


def test_meta_model_not_found_format_falls_back_both_ways(tmp_path):
    models = []

    def handler(request: httpx.Request):
        body = json.loads(request.content)
        models.append(body["model"])
        if body["model"] == "muse-wonder-1.0":
            return httpx.Response(404, json={"error": {"code": "model_not_found", "message": "Unknown model"}})
        return reply('{"detections": []}')

    c = make_client(tmp_path, handler)
    run(c.json(role="wonder", purpose="plan", system="s", text="t", schema=DetectionReply))
    assert models == ["muse-wonder-1.0", "muse-spark-1.3"]


def test_json_mode_off_by_default(tmp_path):
    seen = {}

    def handler(request):
        seen.update(json.loads(request.content))
        return reply('{"detections": []}')

    c = make_client(tmp_path, handler)
    run(c.json(role="spark", purpose="x", system="s", text="t", schema=DetectionReply))
    assert "response_format" not in seen
