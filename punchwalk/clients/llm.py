"""Structured LLM calls: Muse Spark Open on Baseten (primary), Muse Spark 1.3 on Meta (fallback).

`structured(Model, messages, images=...)` builds a strict JSON schema from the Pydantic model,
sends it as `response_format`, validates the reply, retries once with the validation error,
and falls back to Meta after repeated 5xx/timeouts on Baseten (provider="meta-fallback").
Raw reasoning (`reasoning_content`, `<think>` blocks) is dropped; schemas carry a short
`rationale` field instead.
"""

from __future__ import annotations

import base64
import copy
import io
import json
import logging
import re
import threading
import time
from collections.abc import Sequence
from pathlib import Path
from typing import Any, TypeVar

import openai
from PIL import Image
from pydantic import BaseModel, ValidationError

from punchwalk.clients import calls, cost
from punchwalk.clients.base import (
    PROVIDER_BASETEN,
    PROVIDER_META,
    PROVIDER_META_FALLBACK,
    ApiError,
    sha256_bytes,
)
from punchwalk.clients.cache import get_cache, make_key
from punchwalk.config import get_settings

log = logging.getLogger(__name__)
M = TypeVar("M", bound=BaseModel)
ENDPOINT = "/chat/completions"
MAX_IMAGES = 8
IMAGE_LONG_SIDE = 1600
JPEG_QUALITY = 85
TRANSIENT_ATTEMPTS = 3  # first try + 2 retries before falling back

# response_format modes per provider, tried in order; a 400 that rejects the format moves on.
_FORMAT_ORDER = ("json_schema", "json_object", "none")
_format_mode: dict[str, str] = {}
# Baseten model slug that answered (SPARK_OPEN_MODEL, else the first alternate that is not 404).
_resolved_model: dict[str, str] = {}
# Slugs that answered 404 in this process: never sent live again (PLAN 10.9; logged once). Cache lookups
# still try every candidate, so recorded answers and cache keys are exactly as before.
_dead_models: set[str] = set()
_models_lock = threading.Lock()


def _mark_dead(provider: str, model: str, instead: str) -> None:
    with _models_lock:
        if model in _dead_models:
            return
        _dead_models.add(model)
        _resolved_model.setdefault(provider, instead)
    log.warning("%s: model %s is not found (404); using %s for the rest of this process", provider, model, instead)


def live_candidates(provider: str) -> list[str]:
    """Candidates for a live call: the ones that have not answered 404 in this process (all if none left)."""
    cands = model_candidates(provider)
    return [m for m in cands if m not in _dead_models] or cands


class StructuredOutputError(RuntimeError):
    """The model's reply failed validation twice. The pipeline marks the item needs_review."""

    def __init__(self, message: str, *, last_content: str = "", record: calls.CallRecord | None = None):
        super().__init__(message)
        self.last_content = last_content[:2000]
        self.record = record


# --------------------------------------------------------------------------- schema


def strict_schema(model: type[BaseModel]) -> dict[str, Any]:
    """Pydantic model -> strict JSON schema: root object, refs inlined, additionalProperties
    false and every key required everywhere, Optional[X] -> nullable X, no oneOf/allOf."""
    raw = model.model_json_schema()
    defs = raw.pop("$defs", {})

    # Annotation keywords dropped from *schema nodes* (never from a `properties` mapping, whose
    # keys are field names: a field called `title` or `default` must survive).
    annotations = ("title", "default", "discriminator")

    def resolve(node: Any) -> Any:
        """Resolve one schema node."""
        if not isinstance(node, dict):
            return node
        if "$ref" in node:
            name = node["$ref"].split("/")[-1]
            merged = {**copy.deepcopy(defs[name]), **{k: v for k, v in node.items() if k != "$ref"}}
            return resolve(merged)
        if "allOf" in node and len(node["allOf"]) == 1:
            rest = {k: v for k, v in node.items() if k != "allOf"}
            return resolve({**node["allOf"][0], **rest})
        if "oneOf" in node:
            node = {**{k: v for k, v in node.items() if k != "oneOf"}, "anyOf": node["oneOf"]}
        out: dict[str, Any] = {}
        for key, value in node.items():
            if key in annotations:
                continue
            if key == "properties":
                out[key] = {field: resolve(sub) for field, sub in value.items()}
            elif key in ("anyOf", "prefixItems"):
                out[key] = [resolve(v) for v in value]
            elif key in ("items", "additionalProperties") and isinstance(value, dict):
                out[key] = resolve(value)
            else:
                out[key] = value  # enum, const, type, description, bounds...: copied as-is
        if "anyOf" in out:
            variants = out["anyOf"]
            non_null = [v for v in variants if v.get("type") != "null"]
            has_null = len(non_null) != len(variants)
            if has_null and len(non_null) == 1 and non_null[0].get("type") in (
                "string", "number", "integer", "boolean", "array",
            ):
                v = dict(non_null[0])
                v["type"] = [v["type"], "null"]
                if "enum" in v:
                    v["enum"] = [*v["enum"], None]
                out.pop("anyOf")
                out = {**out, **v}
        if out.get("type") == "object" or "properties" in out:
            props = out.get("properties", {})
            out["type"] = "object"
            out["properties"] = props
            out["required"] = list(props.keys())
            out["additionalProperties"] = False
        return out

    schema = resolve(raw)
    if schema.get("type") != "object":
        raise TypeError(f"{model.__name__}: strict schemas need an object root")
    return schema


# --------------------------------------------------------------------------- images


def prepare_image(image: bytes | str | Path) -> bytes:
    """Any image -> RGB JPEG, long side <= 1600 px, quality 85."""
    data = image if isinstance(image, bytes) else Path(image).read_bytes()
    with Image.open(io.BytesIO(data)) as im:
        im = im.convert("RGB")
        w, h = im.size
        scale = IMAGE_LONG_SIDE / max(w, h)
        if scale < 1:
            im = im.resize((max(1, round(w * scale)), max(1, round(h * scale))), Image.LANCZOS)
        buf = io.BytesIO()
        im.save(buf, format="JPEG", quality=JPEG_QUALITY, optimize=True)
        return buf.getvalue()


def _image_part(jpeg: bytes) -> dict[str, Any]:
    return {"type": "image_url", "image_url": {"url": "data:image/jpeg;base64," + base64.b64encode(jpeg).decode()}}


def _attach_images(messages: list[dict[str, Any]], jpegs: list[bytes]) -> list[dict[str, Any]]:
    """Append images to the last user message (as content parts)."""
    if not jpegs:
        return messages
    msgs = copy.deepcopy(messages)
    for m in reversed(msgs):
        if m.get("role") == "user":
            content = m.get("content")
            parts = [{"type": "text", "text": content}] if isinstance(content, str) else list(content or [])
            m["content"] = parts + [_image_part(j) for j in jpegs]
            return msgs
    msgs.append({"role": "user", "content": [_image_part(j) for j in jpegs]})
    return msgs


def _redact_images(value: Any) -> Any:
    """Replace inline base64 images by their sha256 (for cache keys and logs)."""
    if isinstance(value, dict):
        if value.get("type") == "image_url":
            url = (value.get("image_url") or {}).get("url", "")
            if url.startswith("data:"):
                return {"type": "image_url", "sha256": sha256_bytes(url.encode())}
        return {k: _redact_images(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_redact_images(v) for v in value]
    return value


# --------------------------------------------------------------------------- reply handling

_THINK_RE = re.compile(r"<think>.*?</think>", re.DOTALL | re.IGNORECASE)
_FENCE_RE = re.compile(r"^```(?:json)?\s*|\s*```$", re.IGNORECASE)


def extract_json(content: str) -> Any:
    """Strip reasoning blocks / code fences and parse the first JSON object in `content`."""
    text = _THINK_RE.sub("", content or "")
    if "</think>" in text.lower():  # an unclosed opener: keep what follows the last closer
        text = re.split(r"</think>", text, flags=re.IGNORECASE)[-1]
    text = _FENCE_RE.sub("", text.strip()).strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        start = text.find("{")
        if start < 0:
            raise
        decoder = json.JSONDecoder()
        obj, _ = decoder.raw_decode(text[start:])
        return obj


def _schema_instruction(name: str, schema: dict[str, Any]) -> str:
    return (
        f"Reply with only one JSON object (no prose, no code fences) matching this JSON schema "
        f"named {name}:\n{json.dumps(schema, separators=(',', ':'))}"
    )


# --------------------------------------------------------------------------- providers


def model_candidates(provider: str) -> list[str]:
    s = get_settings()
    if provider != PROVIDER_BASETEN:
        return [s.SPARK_FALLBACK_MODEL]
    seen = [_resolved_model[provider]] if provider in _resolved_model else []
    for m in [s.SPARK_OPEN_MODEL, *s.SPARK_OPEN_MODEL_ALTERNATES]:
        if m not in seen:
            seen.append(m)
    return seen


# Tests set this to return an `httpx2.Client` with a MockTransport (openai 3.x sends through
# httpx2, which respx does not patch). None = the SDK's default client.
http_client_factory = None


def _client(provider: str) -> openai.OpenAI:
    s = get_settings()
    if provider == PROVIDER_BASETEN:
        key, base, missing = s.BASETEN_API_KEY.get_secret_value(), s.BASETEN_API_BASE, "BASETEN_API_KEY"
    else:
        key, base, missing = s.MODEL_API_KEY.get_secret_value(), s.META_API_BASE, "MODEL_API_KEY"
    if not key:
        raise ApiError(provider, ENDPOINT, None, f"{missing} is not set (add it to .env)")
    extra = {"http_client": http_client_factory()} if http_client_factory else {}
    return openai.OpenAI(api_key=key, base_url=base, max_retries=0, timeout=s.HTTP_TIMEOUT_S, **extra)


def _build_request(
    provider: str, model: str, messages: list[dict[str, Any]], name: str, schema: dict[str, Any],
    reasoning_effort: str, max_tokens: int, fmt: str,
) -> dict[str, Any]:
    req: dict[str, Any] = {"model": model, "messages": messages, "max_tokens": max_tokens}
    if fmt == "json_schema":
        req["response_format"] = {"type": "json_schema", "json_schema": {"name": name, "strict": True, "schema": schema}}
    elif fmt == "json_object":
        req["response_format"] = {"type": "json_object"}
    if provider == PROVIDER_BASETEN:
        req["reasoning_effort"] = reasoning_effort
    else:
        req["max_tokens"] = max(max_tokens, 4000)  # reasoning on: >= 4000 per the docs
    return req


def _send(provider: str, req: dict[str, Any]) -> dict[str, Any]:
    client, model = _client(provider), req["model"]
    kwargs = dict(req)
    extra: dict[str, Any] = {}
    if "reasoning_effort" in kwargs:
        extra["reasoning_effort"] = kwargs.pop("reasoning_effort")
    try:
        resp = client.chat.completions.create(**kwargs, extra_body=extra or None)
    except openai.APIStatusError as e:
        try:
            body = e.response.text
        except Exception:  # noqa: BLE001
            body = str(e)
        raise ApiError(provider, ENDPOINT, e.status_code, body, model=model) from e
    except (openai.APITimeoutError, openai.APIConnectionError) as e:
        raise ApiError(provider, ENDPOINT, None, f"{type(e).__name__}: {e!s}", model=model) from e
    data = resp.model_dump()
    # Never keep raw reasoning.
    for ch in data.get("choices") or []:
        msg = ch.get("message") or {}
        for k in ("reasoning_content", "reasoning"):
            msg.pop(k, None)
    return data


def _rejects_format(err: ApiError) -> bool:
    text = err.body.lower()
    return err.status == 400 and any(w in text for w in ("response_format", "json_schema", "json mode", "schema", "json_object"))


def _send_with_retries(provider: str, req: dict[str, Any]) -> dict[str, Any]:
    """Send with up to TRANSIENT_ATTEMPTS tries on 5xx/429/timeouts."""
    attempt = 0
    while True:
        attempt += 1
        try:
            return _send(provider, req)
        except ApiError as err:
            if err.transient and attempt < TRANSIENT_ATTEMPTS:
                time.sleep(min(2.0**attempt, 20.0))
                continue
            raise


def _complete(
    provider: str, label: str, messages: list[dict[str, Any]], name: str, schema: dict[str, Any],
    reasoning_effort: str, max_tokens: int, stage: str,
) -> tuple[str, calls.CallRecord]:
    """One completion (model slug fallback, cache, transient retries, response_format
    downgrade). Returns content."""
    candidates = model_candidates(provider)
    # Replay first: a cached answer from any candidate slug beats a live call (and keeps
    # readonly replays working when the first slug is the one that 404s).
    for model in candidates:
        hit = _complete_model(provider, label, model, messages, name, schema, reasoning_effort, max_tokens, stage,
                              live=False)
        if hit is not None:
            _resolved_model[provider] = model
            return hit
    live = live_candidates(provider)
    for i, model in enumerate(live):
        try:
            result = _complete_model(
                provider, label, model, messages, name, schema, reasoning_effort, max_tokens, stage, live=True
            )
        except ApiError as err:
            if err.status == 404 and i + 1 < len(live):
                _mark_dead(provider, model, live[i + 1])
                continue  # unknown model slug: try the next one, and skip this one from now on
            raise
        assert result is not None
        _resolved_model[provider] = model
        return result
    raise AssertionError("unreachable")


def _complete_model(
    provider: str, label: str, model: str, messages: list[dict[str, Any]], name: str, schema: dict[str, Any],
    reasoning_effort: str, max_tokens: int, stage: str, *, live: bool,
) -> tuple[str, calls.CallRecord] | None:
    """With live=False only consult the cache (None on a miss)."""
    cache = get_cache()
    start_fmt = _format_mode.get(provider, "json_schema")
    last_err: ApiError | None = None
    for fmt in _FORMAT_ORDER[_FORMAT_ORDER.index(start_fmt):]:
        msgs = messages if fmt == "json_schema" else _with_schema_instruction(messages, name, schema)
        req = _build_request(provider, model, msgs, name, schema, reasoning_effort, max_tokens, fmt)
        key = make_key(provider, ENDPOINT, model, _redact_images(req))
        base = dict(provider=label, model=model, endpoint=ENDPOINT, stage=stage, cache_key=key, unit_kind="tokens")

        hit = cache.get(key)
        if hit is not None:
            usage = (hit.response or {}).get("usage") or {}
            rec = calls.CallRecord(
                **base, cached=True, tokens_in=usage.get("prompt_tokens"), tokens_out=usage.get("completion_tokens")
            )
            return _content(hit.response), calls.emit(rec)
        if not live:
            continue
        cache.require_live(key, f"{label} {model} structured({name}) format={fmt} stage={stage or '-'}")
        # conservative: ~3 chars per input token (images count as their base64 length) + full max_tokens out
        cost.assert_under_cap(cost.llm_cost(provider, len(json.dumps(req)) // 3, req["max_tokens"]))

        t0 = time.monotonic()
        try:
            data = _send_with_retries(provider, req)
        except ApiError as err:
            calls.emit(calls.CallRecord(
                **base, ok=False, error=str(err)[:500], latency_ms=int((time.monotonic() - t0) * 1000)
            ))
            if _rejects_format(err) and fmt != "none":
                last_err = err
                continue  # try the next response_format mode
            raise
        usage = data.get("usage") or {}
        rec = calls.CallRecord(
            **base, latency_ms=int((time.monotonic() - t0) * 1000),
            tokens_in=usage.get("prompt_tokens"), tokens_out=usage.get("completion_tokens"),
        )
        rec.units = float((rec.tokens_in or 0) + (rec.tokens_out or 0))
        rec.est_cost_usd = cost.llm_cost(provider, rec.tokens_in, rec.tokens_out)
        calls.emit(rec)
        _format_mode[provider] = fmt
        cache.put(
            key, provider=provider, endpoint=ENDPOINT, model=model,
            description=f"structured({name}) stage={stage or '-'} format={fmt}", response=data,
        )
        return _content(data), rec
    if not live:
        return None
    raise last_err or ApiError(provider, ENDPOINT, 400, "every response_format mode was rejected", model=model)


def _with_schema_instruction(messages: list[dict[str, Any]], name: str, schema: dict[str, Any]) -> list[dict[str, Any]]:
    return [{"role": "system", "content": _schema_instruction(name, schema)}, *messages]


def _content(data: dict[str, Any]) -> str:
    choices = data.get("choices") or [{}]
    return ((choices[0].get("message") or {}).get("content")) or ""


# --------------------------------------------------------------------------- public API


def structured(
    schema_model: type[M],
    messages: list[dict[str, Any]],
    images: Sequence[bytes | str | Path] = (),
    reasoning_effort: str = "low",
    max_tokens: int = 1500,
    stage: str = "",
) -> tuple[M, calls.CallRecord]:
    """Ask Spark for one `schema_model` instance. Raises StructuredOutputError after a failed
    repair retry, ApiError when every provider fails."""
    if len(images) > MAX_IMAGES:
        raise ValueError(f"at most {MAX_IMAGES} images per call (got {len(images)})")
    s = get_settings()
    name = re.sub(r"[^A-Za-z0-9_-]", "_", schema_model.__name__)[:64]
    schema = strict_schema(schema_model)
    msgs = _attach_images(messages, [prepare_image(i) for i in images])

    providers = (
        [(PROVIDER_BASETEN, PROVIDER_BASETEN), (PROVIDER_META, PROVIDER_META_FALLBACK)]
        if s.REASONING_PROVIDER == "baseten"
        else [(PROVIDER_META, PROVIDER_META)]
    )
    last_err: ApiError | None = None
    for provider, label in providers:
        try:
            return _validated(schema_model, provider, label, msgs, name, schema, reasoning_effort, max_tokens, stage)
        except ApiError as err:
            last_err = err
            if err.auth_failed and provider == PROVIDER_BASETEN:
                continue  # a bad Baseten key should still fall back
            if not err.transient and provider == PROVIDER_BASETEN and not _rejects_format(err):
                raise
            continue
    assert last_err is not None
    raise last_err


def _validated(
    schema_model: type[M], provider: str, label: str, msgs: list[dict[str, Any]], name: str,
    schema: dict[str, Any], reasoning_effort: str, max_tokens: int, stage: str,
) -> tuple[M, calls.CallRecord]:
    content, rec = _complete(provider, label, msgs, name, schema, reasoning_effort, max_tokens, stage)
    try:
        return schema_model.model_validate(extract_json(content)), rec
    except (ValidationError, json.JSONDecodeError, ValueError) as err:
        first_error = _short_error(err)
    repair = [
        *msgs,
        {"role": "assistant", "content": content[:4000]},
        {
            "role": "user",
            "content": f"Your reply did not match the {name} schema: {first_error}\n"
            "Reply again with only the corrected JSON object.",
        },
    ]
    content2, rec2 = _complete(provider, label, repair, name, schema, reasoning_effort, max_tokens, stage)
    try:
        return schema_model.model_validate(extract_json(content2)), rec2
    except (ValidationError, json.JSONDecodeError, ValueError) as err:
        raise StructuredOutputError(
            f"{label} reply failed {name} validation twice: {_short_error(err)}", last_content=content2, record=rec2
        ) from err


def _short_error(err: Exception) -> str:
    if isinstance(err, ValidationError):
        return "; ".join(f"{'.'.join(str(p) for p in e['loc']) or '<root>'}: {e['msg']}" for e in err.errors()[:8])
    return str(err)[:300]
