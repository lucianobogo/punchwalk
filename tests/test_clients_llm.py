import base64
import io
import json
from typing import Literal

import httpx2
import pytest
from PIL import Image
from pydantic import BaseModel, Field

from punchwalk.clients import llm
from punchwalk.clients.base import ApiError
from punchwalk.clients.cache import make_key
from tests.conftest import FIXTURES, PROBE

BASETEN = "https://inference.baseten.co/v1/chat/completions"
META = "https://api.meta.ai/v1/chat/completions"
RECORDED = json.loads((FIXTURES / "clients" / "baseten_chat_response.json").read_text())


class Box(BaseModel):
    x1: int
    y1: int
    x2: int
    y2: int


class Item(BaseModel):
    title: str = Field(max_length=60)
    severity: Literal["cosmetic", "functional", "safety"]
    trade: Literal["electrical", "plumbing"] | None
    defect_box: Box | None
    tags: list[str]
    parts: list[Box]
    confidence: float = Field(ge=0, le=1)
    rationale: str


GOOD = {"title": "Knob missing", "severity": "cosmetic", "trade": None, "defect_box": {"x1": 1, "y1": 2, "x2": 3, "y2": 4},
        "tags": ["kitchen"], "parts": [], "confidence": 0.8, "rationale": "Visible gap."}


class Router:
    """Minimal respx stand-in for the openai SDK's httpx2 transport."""

    def __init__(self):
        self.routes: dict[str, Route] = {}

    def post(self, url: str) -> "Route":
        return self.routes.setdefault(url, Route())

    def handle(self, request: httpx2.Request) -> httpx2.Response:
        route = self.routes.get(str(request.url))
        if route is None:
            raise AssertionError(f"unexpected request to {request.url}")
        return route(request)


class Route:
    def __init__(self):
        self.calls: list[httpx2.Request] = []
        self._fn = None

    @property
    def call_count(self) -> int:
        return len(self.calls)

    def mock(self, return_value=None, side_effect=None):
        if callable(side_effect):
            self._fn = side_effect
        elif side_effect is not None:
            it = iter(side_effect)
            self._fn = lambda req: next(it)
        else:
            self._fn = lambda req: return_value
        return self

    def __call__(self, request):
        request.read()
        self.calls.append(request)
        return self._fn(request)


@pytest.fixture
def respx(monkeypatch):
    router = Router()
    monkeypatch.setattr(llm, "http_client_factory", lambda: httpx2.Client(transport=httpx2.MockTransport(router.handle)))
    return router


def json_response(status: int, body) -> httpx2.Response:
    return httpx2.Response(status, json=body)


def chat(content: str, prompt_tokens: int = 100, completion_tokens: int = 50) -> httpx2.Response:
    return httpx2.Response(200, json={
        "id": "x", "object": "chat.completion", "created": 1, "model": "m",
        "choices": [{"index": 0, "finish_reason": "stop",
                     "message": {"role": "assistant", "content": content, "reasoning_content": "secret thoughts"}}],
        "usage": {"prompt_tokens": prompt_tokens, "completion_tokens": completion_tokens, "total_tokens": 150},
    })


def walk(node, fn):
    fn(node)
    if isinstance(node, dict):
        for v in node.values():
            walk(v, fn)
    elif isinstance(node, list):
        for v in node:
            walk(v, fn)


def test_strict_schema_rules():
    schema = llm.strict_schema(Item)
    text = json.dumps(schema)
    assert "$ref" not in text and "oneOf" not in text and "allOf" not in text and "$defs" not in text
    assert schema["type"] == "object"

    def check(n):
        if isinstance(n, dict) and n.get("type") == "object" or (isinstance(n, dict) and "properties" in n):
            assert n["additionalProperties"] is False
            assert n["required"] == list(n["properties"])
    walk(schema, check)
    props = schema["properties"]
    assert "title" in props and set(props) == set(Item.model_fields)
    assert props["title"] == {"type": "string", "maxLength": 60}
    assert props["trade"]["type"] == ["string", "null"] and None in props["trade"]["enum"]
    assert {v.get("type") for v in props["defect_box"]["anyOf"]} == {"object", "null"}
    assert props["parts"]["items"]["additionalProperties"] is False


class Annotated(BaseModel):
    """Field names that collide with JSON-schema annotation keywords."""

    title: str
    default: int | None
    discriminator: str
    description: str
    sub: Box
    subs: list[Box]
    rationale: str


def test_strict_schema_keeps_fields_named_like_keywords():
    schema = llm.strict_schema(Annotated)
    props = schema["properties"]
    assert set(props) == set(Annotated.model_fields) and schema["required"] == list(Annotated.model_fields)
    assert props["title"]["type"] == "string" and props["default"]["type"] == ["integer", "null"]
    assert "title" not in props["sub"] and props["sub"]["required"] == ["x1", "y1", "x2", "y2"]
    assert "title" not in schema  # the model's own title annotation is still dropped
    Annotated.model_validate({"title": "t", "default": None, "discriminator": "d", "description": "x",
                              "sub": {"x1": 0, "y1": 0, "x2": 1, "y2": 1}, "subs": [], "rationale": "r"})


def test_extract_json_strips_reasoning_and_fences():
    assert llm.extract_json('<think>hmm {"a": 0}</think>\n```json\n{"a": 1}\n```') == {"a": 1}
    assert llm.extract_json('Sure! {"a": 2} hope that helps') == {"a": 2}


def test_prepare_image_downscales_to_jpeg():
    buf = io.BytesIO()
    Image.new("RGBA", (3200, 1000), (10, 20, 30, 255)).save(buf, "PNG")
    out = Image.open(io.BytesIO(llm.prepare_image(buf.getvalue())))
    assert out.format == "JPEG" and out.size == (1600, 500)


def test_structured_success_sends_schema_images_and_effort(respx, no_fixture_cache, ledger_rows, readwrite):
    route = respx.post(BASETEN).mock(return_value=chat(json.dumps(GOOD)))
    inst, rec = llm.structured(Item, [{"role": "user", "content": "write it"}], images=[PROBE / "media" / "frame.jpg"],
                               stage="write_items")
    assert inst.title == "Knob missing" and rec.provider == "baseten" and rec.stage == "write_items"
    body = json.loads(route.calls[0].content)
    assert body["model"] == "meta-models/muse-spark-open-1026"
    assert body["reasoning_effort"] == "low" and body["max_tokens"] == 1500
    assert body["response_format"]["type"] == "json_schema" and body["response_format"]["json_schema"]["strict"] is True
    img = body["messages"][-1]["content"][-1]
    assert img["type"] == "image_url" and img["image_url"]["url"].startswith("data:image/jpeg;base64,")
    assert max(Image.open(io.BytesIO(base64.b64decode(img["image_url"]["url"].split(",", 1)[1]))).size) <= 1600
    assert rec.est_cost_usd == pytest.approx(100 * 1.25e-6 + 50 * 4.25e-6)
    assert ledger_rows()[-1]["provider"] == "baseten"
    # cached responses never keep reasoning
    from punchwalk.clients.cache import get_cache
    entry = get_cache().get(rec.cache_key)
    assert "reasoning_content" not in json.dumps(entry.response)


def test_repair_retry_appends_validation_error(respx, no_fixture_cache, readwrite):
    bad = dict(GOOD, severity="catastrophic")
    route = respx.post(BASETEN).mock(side_effect=[chat(json.dumps(bad)), chat(json.dumps(GOOD))])
    inst, _ = llm.structured(Item, [{"role": "user", "content": "x"}])
    assert inst.severity == "cosmetic"
    second = json.loads(route.calls[1].content)["messages"]
    assert second[-2]["role"] == "assistant" and "severity" in second[-1]["content"]


def test_two_validation_failures_raise_structured_output_error(respx, no_fixture_cache, readwrite):
    respx.post(BASETEN).mock(side_effect=[chat("not json"), chat('{"title": 5}')])
    with pytest.raises(llm.StructuredOutputError, match="validation twice"):
        llm.structured(Item, [{"role": "user", "content": "x"}])


def test_fallback_to_meta_after_repeated_5xx(respx, no_fixture_cache, ledger_rows, readwrite):
    b = respx.post(BASETEN).mock(return_value=json_response(503, {"error": "overloaded"}))
    m = respx.post(META).mock(return_value=chat(json.dumps(GOOD)))
    inst, rec = llm.structured(Item, [{"role": "user", "content": "x"}])
    assert b.call_count == 3  # first try + 2 retries
    assert rec.provider == "meta-fallback" and inst.title == "Knob missing"
    meta_body = json.loads(m.calls[0].content)
    assert meta_body["model"] == "muse-spark-1.3" and meta_body["max_tokens"] >= 4000
    assert "reasoning_effort" not in meta_body
    assert [r["provider"] for r in ledger_rows()] == ["baseten", "meta-fallback"]


def test_unknown_slug_falls_through_to_alternate(respx, no_fixture_cache, readwrite):
    def handler(request):
        model = json.loads(request.content)["model"]
        if model.endswith("-1026"):
            return json_response(404, {"error": "please check the model you provided"})
        return chat(json.dumps(GOOD))
    respx.post(BASETEN).mock(side_effect=handler)
    _, rec = llm.structured(Item, [{"role": "user", "content": "x"}])
    assert rec.model == "meta-models/muse-spark-open" and llm._resolved_model["baseten"] == rec.model


def test_a_404_slug_is_skipped_for_the_rest_of_the_process(respx, no_fixture_cache, readwrite, caplog):
    """PLAN 10.9: the first 404 on the hackathon slug moves every later call (any prompt, even after the
    catalog slug failed once) straight to the catalog slug; logged once; cache keys don't change."""
    import logging

    sent: list[str] = []
    state = {"fail_next": False}

    def handler(request):
        model = json.loads(request.content)["model"]
        sent.append(model)
        if model.endswith("-1026"):
            return json_response(404, {"error": "please check the model you provided"})
        if state["fail_next"]:
            state["fail_next"] = False
            return json_response(400, {"error": "bad request"})
        return chat(json.dumps(GOOD))

    respx.post(BASETEN).mock(side_effect=handler)
    with caplog.at_level(logging.WARNING, logger="punchwalk.clients.llm"):
        llm.structured(Item, [{"role": "user", "content": "first"}])
        llm._resolved_model.clear()  # even without the success memo, the 404 itself is remembered
        state["fail_next"] = True
        with pytest.raises(ApiError):
            llm.structured(Item, [{"role": "user", "content": "second"}])
        _, rec = llm.structured(Item, [{"role": "user", "content": "third"}])
    assert sent == ["meta-models/muse-spark-open-1026", "meta-models/muse-spark-open",
                    "meta-models/muse-spark-open", "meta-models/muse-spark-open"]
    assert rec.model == "meta-models/muse-spark-open"
    notes = [r.getMessage() for r in caplog.records if "404" in r.getMessage()]
    assert len(notes) == 1 and "muse-spark-open-1026" in notes[0]
    # the cache key is the request's (model included): a replay of "third" finds it under the catalog slug
    req = llm._build_request("baseten", "meta-models/muse-spark-open",
                             [{"role": "user", "content": "third"}], "Item", llm.strict_schema(Item), "low", 1500,
                             "json_schema")
    assert rec.cache_key == make_key("baseten", llm.ENDPOINT, "meta-models/muse-spark-open", llm._redact_images(req))


def test_rejected_json_schema_downgrades_to_json_object(respx, no_fixture_cache, readwrite):
    route = respx.post(BASETEN).mock(side_effect=[
        json_response(400, {"error": "response_format json_schema is not supported for this model"}),
        chat(json.dumps(GOOD)),
    ])
    llm.structured(Item, [{"role": "user", "content": "x"}])
    second = json.loads(route.calls[1].content)
    assert second["response_format"] == {"type": "json_object"}
    assert "JSON schema" in second["messages"][0]["content"]
    assert llm._format_mode["baseten"] == "json_object"


def test_meta_provider_only(respx, monkeypatch, no_fixture_cache, readwrite):
    from punchwalk.config import reset_settings
    monkeypatch.setenv("REASONING_PROVIDER", "meta")
    reset_settings()
    respx.post(META).mock(return_value=chat(json.dumps(GOOD)))
    _, rec = llm.structured(Item, [{"role": "user", "content": "x"}])
    assert rec.provider == "meta"


def test_too_many_images():
    with pytest.raises(ValueError):
        llm.structured(Item, [{"role": "user", "content": "x"}], images=[b"x"] * 9)


def test_readonly_replay_of_probe_spark_call():
    import importlib.util
    spec = importlib.util.spec_from_file_location("probe", FIXTURES.parent / "scripts" / "probe.py")
    probe = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(probe)
    inst, rec = llm.structured(probe.ProbeSeen, probe.SPARK_MESSAGES, images=[PROBE / "media" / "frame.jpg"],
                               stage="probe")
    assert rec.cached and rec.provider == "baseten" and rec.model == RECORDED["model"]
    assert inst.color and "blue" in inst.color.lower()
